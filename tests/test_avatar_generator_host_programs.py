"""The optional DiceBear avatar starts node only by full path.

No trusted node means the PIL avatar, and no program is started at all. A
trusted node runs from host_programs' fixed working folder with its small
environment. No real program is ever run here.
"""

from __future__ import annotations

import os
import shutil
import subprocess

import pytest

from puffpai import avatar_generator
from puffpai.security import host_programs


@pytest.fixture(autouse=True)
def _no_which(monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("shutil.which was asked")
    monkeypatch.setattr(shutil, "which", boom)


@pytest.fixture
def gen(tmp_path, monkeypatch):
    monkeypatch.setattr(avatar_generator, "_CACHE_DIR", str(tmp_path / "cache"))
    return avatar_generator.AvatarGenerator()


@pytest.fixture
def runs(monkeypatch):
    calls = []

    def run(argv, **kw):
        calls.append((list(argv), kw))
        return subprocess.CompletedProcess(argv, 0, stdout="v22.12.0", stderr="")

    monkeypatch.setattr(avatar_generator.subprocess, "run", run)
    return calls


def test_no_trusted_node_means_no_program_and_the_pil_avatar(gen, runs, monkeypatch):
    monkeypatch.setattr(host_programs, "path", lambda name: None)
    assert gen._check_node() is False
    img = gen._from_dicebear("Sam", "adventurer", 32)
    assert img.size == (32, 32)
    assert runs == []


def test_a_planted_node_in_the_working_folder_is_never_run(gen, runs, tmp_path,
                                                           monkeypatch):
    planted = tmp_path / "node.exe"
    planted.write_bytes(b"")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.setattr(host_programs, "path", lambda name: None)
    gen.generate("Sam", use_dicebear=True, size=16)
    assert runs == []
    assert planted.read_bytes() == b""


def test_the_version_check_uses_the_trusted_node(gen, runs, tmp_path, monkeypatch):
    node = str(tmp_path / "nodejs" / "node.exe")
    monkeypatch.setattr(host_programs, "path", lambda name: node if name == "node" else None)
    assert gen._check_node() is True
    (argv, kw), = runs
    assert argv == [node, "--version"]
    assert kw["cwd"] == host_programs.SAFE_CWD
    assert set(kw["env"]) <= set(host_programs.run_kwargs()["env"])


def test_dicebear_runs_on_the_trusted_node_from_the_fixed_folder(gen, runs, tmp_path,
                                                                 monkeypatch):
    node = str(tmp_path / "nodejs" / "node.exe")
    monkeypatch.setattr(host_programs, "path", lambda name: node if name == "node" else None)
    avatar_dir = tmp_path / "avatar"
    avatar_dir.mkdir()
    (avatar_dir / "generate_avatar.js").write_text("")
    monkeypatch.setattr(avatar_generator, "_AVATAR_DIR", str(avatar_dir))
    gen._from_dicebear("Sam", "adventurer", 32)  # bad stdout: falls back to PIL
    (argv, kw), = runs
    assert argv[0] == node
    assert argv[1] == str(avatar_dir / "generate_avatar.js")
    assert kw["cwd"] == host_programs.SAFE_CWD


def test_no_avatar_script_means_no_program(gen, runs, tmp_path, monkeypatch):
    node = str(tmp_path / "nodejs" / "node.exe")
    monkeypatch.setattr(host_programs, "path", lambda name: node)
    monkeypatch.setattr(avatar_generator, "_AVATAR_DIR", str(tmp_path / "absent"))
    assert gen._from_dicebear("Sam", "adventurer", 32).size == (32, 32)
    assert runs == []
