"""Small host-program checks.

* ``host_programs.npm_cli`` finds npm on Homebrew (node is a link into
  ``Cellar/node/<version>/bin``; npm lives in that version's ``libexec`` and
  in ``<brew>/lib/node_modules``), and still on nvm and nodejs.org layouts.
  All synthetic folders; nothing is run.
* ``puffpai start`` says why when Node.js vanishes between its two looks.
* ``api_server._write_key_file`` starts icacls by full path from the system
  folder, never by name.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

import pytest

from puffpai.security import host_programs as hp

TAIL = ("npm", "bin", "npm-cli.js")


def _touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x", encoding="utf-8")
    return path


@pytest.fixture
def posix(monkeypatch, tmp_path):
    """The Linux / macOS branch, with BuildBot's own folders somewhere else."""
    monkeypatch.setattr(hp, "_IS_WINDOWS", False)
    install = tmp_path / "BuildBot"
    install.mkdir()
    monkeypatch.setattr(hp, "_forbidden_roots_for_node", lambda: [str(install)])
    links: dict = {}
    monkeypatch.setattr(hp, "_realpath",
                        lambda p: links.get(os.path.normcase(str(p)), str(p)))
    return {"tmp": tmp_path, "install": install, "links": links}


def _link(posix, link: Path, target: Path) -> None:
    posix["links"][os.path.normcase(str(link))] = str(target)


def test_homebrew_npm_in_the_version_libexec(posix):
    brew = posix["tmp"] / "opt" / "homebrew"
    cellar_node = _touch(brew / "Cellar" / "node" / "22.12.0" / "bin" / "node")
    node = _touch(brew / "bin" / "node")          # stands in for the link
    _link(posix, node, cellar_node)
    npm = _touch(brew / "Cellar" / "node" / "22.12.0" / "libexec" / "lib" / "node_modules"
                 / Path(*TAIL))
    assert hp.npm_cli(str(node)) == str(npm)


def test_homebrew_npm_in_the_brew_prefix(posix):
    brew = posix["tmp"] / "usr" / "local"
    cellar_node = _touch(brew / "Cellar" / "node" / "22.12.0" / "bin" / "node")
    node = _touch(brew / "bin" / "node")
    _link(posix, node, cellar_node)
    npm = _touch(brew / "lib" / "node_modules" / Path(*TAIL))
    assert hp.npm_cli(str(node)) == str(npm)


def test_before_the_fix_homebrew_answered_nothing(posix):
    """The old rule looked only in <real prefix>/lib/node_modules, which on
    Homebrew is Cellar/node/<v>/lib: nothing there."""
    brew = posix["tmp"] / "opt" / "homebrew"
    cellar_node = _touch(brew / "Cellar" / "node" / "22.12.0" / "bin" / "node")
    node = _touch(brew / "bin" / "node")
    _link(posix, node, cellar_node)
    _touch(brew / "lib" / "node_modules" / Path(*TAIL))
    old_candidate = os.path.join(os.path.dirname(os.path.dirname(str(cellar_node))),
                                 "lib", "node_modules", *TAIL)
    assert not os.path.exists(old_candidate)
    assert hp.npm_cli(str(node))


def test_nvm_layout_still_works(posix):
    root = posix["tmp"] / "home" / ".nvm" / "versions" / "node" / "v22.12.0"
    node = _touch(root / "bin" / "node")
    npm = _touch(root / "lib" / "node_modules" / Path(*TAIL))
    assert hp.npm_cli(str(node)) == str(npm)


def test_nodejs_org_mac_package_still_works(posix):
    root = posix["tmp"] / "usr" / "local"
    node = _touch(root / "bin" / "node")
    npm = _touch(root / "lib" / "node_modules" / Path(*TAIL))
    assert hp.npm_cli(str(node)) == str(npm)


def test_an_npm_inside_buildbots_own_folder_is_never_used(posix):
    install = posix["install"]
    node = _touch(posix["tmp"] / "brew" / "bin" / "node")
    _link(posix, node, _touch(install / "Cellar" / "node" / "1" / "bin" / "node"))
    _touch(install / "Cellar" / "node" / "1" / "libexec" / "lib" / "node_modules" / Path(*TAIL))
    assert hp.npm_cli(str(node)) is None


def test_no_npm_anywhere_is_none(posix):
    node = _touch(posix["tmp"] / "x" / "bin" / "node")
    assert hp.npm_cli(str(node)) is None


# ── icacls by full path ──────────────────────────────────────────────────────

def test_icacls_is_known_by_full_path_only(monkeypatch):
    if sys.platform == "win32":
        got = hp.path("icacls")
        assert got and os.path.isabs(got)
        assert os.path.normcase(got) == os.path.normcase(os.path.join(hp.SYSTEM32, "icacls.exe"))
    monkeypatch.setattr(hp, "_IS_WINDOWS", False)
    assert hp.path("icacls") is None


def _key_file_calls(monkeypatch, tmp_path, icacls):
    from puffpai import api_server
    calls = []
    monkeypatch.setattr(api_server.sys, "platform", "win32")
    monkeypatch.setenv("USERNAME", "owner")
    monkeypatch.setattr(hp, "path", lambda name: icacls if name == "icacls" else None)
    monkeypatch.setattr(api_server.subprocess, "run",
                        lambda argv, **kw: calls.append((argv, kw)))
    api_server._write_key_file(str(tmp_path / "api_key.txt"), "not-a-real-key")
    return calls


def test_the_key_file_acl_uses_the_system_icacls(monkeypatch, tmp_path):
    full = r"C:\Windows\System32\icacls.exe"
    calls = _key_file_calls(monkeypatch, tmp_path, full)
    assert len(calls) == 1
    argv, kw = calls[0]
    assert argv[0] == full and argv[2:4] == ["/inheritance:r", "/grant:r"]
    assert kw["stdin"] == subprocess.DEVNULL and kw["cwd"] == hp.SAFE_CWD


def test_without_the_system_icacls_nothing_is_started_by_name(monkeypatch, tmp_path):
    assert _key_file_calls(monkeypatch, tmp_path, None) == []
    assert (tmp_path / "api_key.txt").read_text(encoding="utf-8") == "not-a-real-key"


def test_the_key_file_never_names_icacls_bare():
    import ast
    from puffpai import api_server
    tree = ast.parse(Path(api_server.__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.List, ast.Tuple)) and node.elts:
            first = node.elts[0]
            assert not (isinstance(first, ast.Constant) and str(first.value).lower()
                        in ("icacls", "icacls.exe")), ast.dump(node)[:120]


# ── puffpai start: Node.js vanishing between the two looks ──────────────────

def test_start_says_why_when_node_vanishes(monkeypatch, tmp_path, capsys):
    from puffpai import start_cli as sc
    dash = tmp_path / "dashboard"
    dash.mkdir()
    stopped = []

    class P:
        pid = 4321

        def poll(self):
            return None

    monkeypatch.setattr(sc, "dashboard_dir", lambda: dash)
    monkeypatch.setattr(sc, "_api_key", lambda: "k" * 40)
    monkeypatch.setattr(sc, "answers", lambda url: False)
    monkeypatch.setattr(sc, "wait_up", lambda *a, **k: True)
    monkeypatch.setattr(sc, "_spawn", lambda argv, cwd, env, log: P())
    monkeypatch.setattr(sc, "stop_pid", lambda pid: stopped.append(pid) or True)
    monkeypatch.setattr(sc, "logs_dir", lambda: tmp_path)
    monkeypatch.setattr(sc, "_write_record", lambda rec: None)
    monkeypatch.setattr(sc, "_forget_record", lambda: None)
    monkeypatch.setattr(sc, "_read_record", lambda: {})
    monkeypatch.setattr(sc, "web_page_ready", lambda dash, env, say=print, run=None: True)
    monkeypatch.setattr(sc, "_node", lambda: None)       # gone after web_page_ready
    args = argparse.Namespace(port=None, dashboard_port=None, no_browser=True, lan=False)
    assert sc.cmd_start(args) == 1
    out = capsys.readouterr().out
    lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
    assert lines[0] == "Opening your bot on this computer."
    assert len(lines) >= 3, out                         # not a silent exit
    assert "Node.js" in out and "puffpai start again" in out
    assert stopped == [4321]                            # the bot it started is stopped
    from puffpai import plain_words
    for line in lines[1:]:
        assert not plain_words.check(line), line
