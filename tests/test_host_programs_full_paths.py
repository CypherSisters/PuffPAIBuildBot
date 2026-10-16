"""host_programs: programs by full path only, from a fixed working folder.

A planted ``wsl.exe`` in the current folder (zero bytes, never run) and a
``PATH`` that points at it must not change what :func:`host_programs.path`
answers; ``shutil.which`` is never asked.
"""

from __future__ import annotations

import os
import shutil
import sys

import pytest

from puffpai.security import host_programs

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="Windows paths")


@pytest.fixture(autouse=True)
def _no_which(monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("shutil.which was asked")
    monkeypatch.setattr(shutil, "which", boom)


def _system32():
    return os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")


@windows_only
def test_a_planted_wsl_in_the_current_folder_is_not_used(tmp_path, monkeypatch):
    planted = tmp_path / "wsl.exe"
    planted.write_bytes(b"")  # zero bytes: never run
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ.get("PATH", ""))
    got = host_programs.path("wsl")
    expected = os.path.join(_system32(), "wsl.exe")
    if os.path.isfile(expected):
        assert os.path.normcase(got) == os.path.normcase(expected)
    else:
        assert got is None
    assert got is None or os.path.normcase(got) != os.path.normcase(str(planted))
    assert planted.read_bytes() == b""


@windows_only
def test_run_kwargs_use_system32_and_a_small_environment(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:8080")
    monkeypatch.setenv("NODE_OPTIONS", "--require evil.js")
    kw = host_programs.run_kwargs()
    assert os.path.normcase(kw["cwd"]) == os.path.normcase(_system32())
    assert os.path.normcase(host_programs.SAFE_CWD) == os.path.normcase(_system32())
    env = kw["env"]
    assert set(env) <= {"SystemRoot", "TEMP", "TMP", "PATH", "LOCALAPPDATA",
                        "USERPROFILE"}
    for part in env["PATH"].split(os.pathsep):
        assert os.path.normcase(part).startswith(
            os.path.normcase(os.environ.get("SystemRoot", r"C:\Windows")))
    assert str(tmp_path) not in env["PATH"]


@windows_only
@pytest.mark.parametrize("name,tail", [
    ("taskkill", "taskkill.exe"),
    ("tasklist", "tasklist.exe"),
    ("powershell", os.path.join("WindowsPowerShell", "v1.0", "powershell.exe"))])
def test_system_programs_come_from_system32(name, tail):
    got = host_programs.path(name)
    expected = os.path.join(_system32(), tail)
    if os.path.isfile(expected):
        assert os.path.normcase(got) == os.path.normcase(expected)


@pytest.mark.parametrize("name", list(host_programs.NAMES))
def test_every_answer_is_a_full_path_or_none(name, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    got = host_programs.path(name)
    assert got is None or (os.path.isabs(got) and os.path.isfile(got))
    if got is not None:
        assert not os.path.normcase(got).startswith(os.path.normcase(str(tmp_path)))


def test_an_unknown_name_is_a_mistake():
    for bad in ("curl", "cmd", "wsl.exe", "", "npm"):
        with pytest.raises(ValueError):
            host_programs.path(bad)


def _no_program_files(tmp_path, monkeypatch):
    """Program Files folders with no Node.js in them (this computer's own
    install is not part of the test)."""
    for var in ("ProgramFiles", "ProgramFiles(x86)"):
        monkeypatch.setenv(var, str(tmp_path / var.replace("(", "").replace(")", "")))


@windows_only
def test_node_from_nodejs_own_program_files_folder_without_a_record(tmp_path, monkeypatch):
    """A computer that already has Node.js (nodejs.org's installer, in its
    fixed Program Files folder) is never asked to install it again."""
    install = tmp_path / "install"
    install.mkdir()
    monkeypatch.setattr(host_programs, "_install_root", lambda: str(install))
    _no_program_files(tmp_path, monkeypatch)
    assert host_programs.path("node") is None
    node = tmp_path / "ProgramFiles" / "nodejs" / "node.exe"
    node.parent.mkdir(parents=True)
    node.write_bytes(b"")
    assert host_programs.path("node") == str(node)
    other = tmp_path / "elsewhere" / "node.exe"
    other.parent.mkdir()
    other.write_bytes(b"")
    (install / "node_path.txt").write_text(str(other), encoding="utf-8")
    assert host_programs.path("node") == str(other)      # the record comes first


@windows_only
def test_node_only_from_the_installers_record(tmp_path, monkeypatch):
    install = tmp_path / "install"
    install.mkdir()
    _no_program_files(tmp_path, monkeypatch)
    monkeypatch.setattr(host_programs, "_install_root", lambda: str(install))
    record = install / "node_path.txt"
    assert host_programs.path("node") is None  # no record

    outside = tmp_path / "nodejs" / "node.exe"
    outside.parent.mkdir()
    outside.write_bytes(b"")
    record.write_text(str(outside) + "\n", encoding="utf-8")
    assert host_programs.path("node") == str(outside)

    inside = install / "bin" / "node.exe"
    inside.parent.mkdir()
    inside.write_bytes(b"")
    record.write_text(str(inside), encoding="utf-8")
    assert host_programs.path("node") is None  # inside the install

    for bad in ("node.exe", str(tmp_path / "nodejs" / "other.exe"),
                str(tmp_path / "missing" / "node.exe"),
                f"{outside}\n{outside}"):
        record.write_text(bad, encoding="utf-8")
        assert host_programs.path("node") is None


@windows_only
def test_node_inside_the_state_or_apps_folder_is_refused(tmp_path, monkeypatch):
    install = tmp_path / "install"
    install.mkdir()
    _no_program_files(tmp_path, monkeypatch)
    monkeypatch.setattr(host_programs, "_install_root", lambda: str(install))
    apps = tmp_path / "apps"
    monkeypatch.setenv("PUFFPAI_APPS_ROOT", str(apps))
    for root in (os.environ["PUFFPAI_STATE_DIR"], str(apps)):
        node = os.path.join(root, "n", "node.exe")
        os.makedirs(os.path.dirname(node), exist_ok=True)
        open(node, "wb").close()
        (install / "node_path.txt").write_text(node, encoding="utf-8")
        assert host_programs.path("node") is None


# ---------------------------------------------------------------------------
# npm as npm-cli.js on the trusted node, and the
# installer's node record on Linux and macOS too
# ---------------------------------------------------------------------------

def _touch(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")  # zero bytes: never run
    return path


@windows_only
def test_npm_cli_is_the_one_beside_the_trusted_node(tmp_path, monkeypatch):
    install = tmp_path / "install"
    install.mkdir()
    _no_program_files(tmp_path, monkeypatch)
    monkeypatch.setattr(host_programs, "_install_root", lambda: str(install))
    node = _touch(tmp_path / "nodejs" / "node.exe")
    assert host_programs.npm_cli(str(node)) is None  # no npm beside it
    cli = _touch(tmp_path / "nodejs" / "node_modules" / "npm" / "bin" / "npm-cli.js")
    _touch(tmp_path / "nodejs" / "npm.cmd")  # the batch file is never answered
    assert host_programs.npm_cli(str(node)) == str(cli)
    # default: the node from the installer's record
    assert host_programs.npm_cli() is None  # no record yet
    (install / "node_path.txt").write_text(str(node), encoding="utf-8")
    assert host_programs.npm_cli() == str(cli)


@windows_only
def test_npm_cli_ignores_a_planted_npm_in_the_working_folder(tmp_path, monkeypatch):
    install = tmp_path / "install"
    install.mkdir()
    monkeypatch.setattr(host_programs, "_install_root", lambda: str(install))
    node = _touch(tmp_path / "nodejs" / "node.exe")
    planted = tmp_path / "downloads"
    _touch(planted / "node_modules" / "npm" / "bin" / "npm-cli.js")
    _touch(planted / "npm.cmd")
    monkeypatch.chdir(planted)
    monkeypatch.setenv("PATH", str(planted) + os.pathsep + os.environ.get("PATH", ""))
    assert host_programs.npm_cli(str(node)) is None


def test_npm_cli_needs_a_real_full_path_node(tmp_path):
    assert host_programs.npm_cli("node") is None
    assert host_programs.npm_cli(str(tmp_path / "missing" / "node")) is None
    assert host_programs.npm_cli("") is None


@windows_only
def test_npm_cli_inside_a_buildbot_folder_is_refused(tmp_path, monkeypatch):
    install = tmp_path / "install"
    install.mkdir()
    monkeypatch.setattr(host_programs, "_install_root", lambda: str(install))
    node = _touch(install / "nodejs" / "node.exe")
    _touch(install / "nodejs" / "node_modules" / "npm" / "bin" / "npm-cli.js")
    assert host_programs.npm_cli(str(node)) is None


@pytest.fixture
def unix(monkeypatch, tmp_path):
    """The Linux / macOS branch, on fixed places made under tmp_path."""
    install = tmp_path / "install"
    install.mkdir()
    monkeypatch.setattr(host_programs, "_IS_WINDOWS", False)
    monkeypatch.setattr(host_programs, "_install_root", lambda: str(install))
    fixed = str(_touch(tmp_path / "usr" / "bin" / "node"))
    monkeypatch.setattr(host_programs, "_LINUX_PATHS", {"node": (fixed,)})
    mac = str(_touch(tmp_path / "opt" / "homebrew" / "bin" / "node"))
    monkeypatch.setattr(host_programs, "_MAC_NODE", (mac,))
    return install, fixed, mac


def test_unix_node_prefers_the_installers_record(unix, tmp_path, monkeypatch):
    install, fixed, _mac = unix
    monkeypatch.setattr(sys, "platform", "linux")
    assert host_programs.path("node") == fixed  # no record: the fixed place
    nvm = _touch(tmp_path / "home" / ".nvm" / "v22" / "bin" / "node")
    (install / "node_path.txt").write_text(str(nvm) + "\n", encoding="utf-8")
    assert host_programs.path("node") == str(nvm)


def test_unix_node_record_is_checked_like_windows(unix, tmp_path, monkeypatch):
    install, fixed, _mac = unix
    monkeypatch.setattr(sys, "platform", "linux")
    inside = _touch(install / "bin" / "node")
    other = _touch(tmp_path / "x" / "nodejs-evil")
    for bad in (str(inside), str(other), "node", str(tmp_path / "missing" / "node"),
                f"{fixed}\n{fixed}"):
        (install / "node_path.txt").write_text(bad, encoding="utf-8")
        # a refused record never widens the search: only the fixed place
        assert host_programs.path("node") == fixed


def test_the_mac_places_are_used_only_on_a_mac(unix, monkeypatch):
    _install, fixed, mac = unix
    monkeypatch.setattr(host_programs, "_LINUX_PATHS", {"node": ()})
    monkeypatch.setattr(sys, "platform", "linux")
    assert host_programs.path("node") is None
    monkeypatch.setattr(sys, "platform", "darwin")
    assert host_programs.path("node") == mac


def test_unix_npm_cli_is_in_the_node_prefix(unix, tmp_path):
    node = _touch(tmp_path / "prefix" / "bin" / "node")
    assert host_programs.npm_cli(str(node)) is None
    cli = _touch(tmp_path / "prefix" / "lib" / "node_modules" / "npm" / "bin" / "npm-cli.js")
    _touch(tmp_path / "prefix" / "bin" / "npm")  # the npm script is never answered
    assert host_programs.npm_cli(str(node)) == str(cli)
