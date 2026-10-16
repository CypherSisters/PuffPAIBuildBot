"""The running server picks up a posture or switch change made by another
process (``puffpai outside on`` / ``services on``) without a restart, in both
directions, and a close takes effect on the very next check.

No network: only files in a temp config folder are read.
"""
from __future__ import annotations

import json
import os

import pytest

from puffpai import build_manifest, build_posture
from puffpai.security import egress_doors


class _Cfg:
    def __init__(self, config_dir: str):
        self.CONFIG_DIR = config_dir
        self.REPO_ROOT = config_dir
        self.DATA_DIR = config_dir


def _write(path, data, bump: int) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")
    st = os.stat(path)
    # A later stamp even on a file system with coarse times.
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + bump * 1_000_000_000))


def _manifest(run_egress: str) -> dict:
    posture = build_posture.posture_manifest_block(
        build_posture.assess_posture("online", run_egress, []))
    return {"schema": build_manifest.SCHEMA, "posture": posture}


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    folder = tmp_path / "config"
    folder.mkdir()
    monkeypatch.setattr("puffpai.config.get_config", lambda **kw: _Cfg(str(folder)))
    egress_doors.reset_cache()
    yield folder
    egress_doors.reset_cache()


def test_posture_opened_elsewhere_is_seen_without_a_restart(cfg):
    _write(cfg / "build_manifest.json", _manifest("island"), 0)
    assert egress_doors.run_posture()["run_egress"] == "island"
    # Another process widens it; this one never calls reset_cache().
    _write(cfg / "build_manifest.json", _manifest(egress_doors.OPEN_POSTURE), 1)
    assert egress_doors.run_posture()["run_egress"] == egress_doors.OPEN_POSTURE


def test_posture_closed_elsewhere_takes_effect_at_once(cfg):
    _write(cfg / "build_manifest.json", _manifest(egress_doors.OPEN_POSTURE), 0)
    assert egress_doors.run_posture()["run_egress"] == egress_doors.OPEN_POSTURE
    _write(cfg / "build_manifest.json", _manifest("island"), 1)
    assert egress_doors.run_posture()["run_egress"] == "island"


def test_a_manifest_removed_elsewhere_falls_back_to_the_safe_default(cfg):
    _write(cfg / "build_manifest.json", _manifest(egress_doors.OPEN_POSTURE), 0)
    assert egress_doors.run_posture()["run_egress"] == egress_doors.OPEN_POSTURE
    os.remove(cfg / "build_manifest.json")
    assert egress_doors.run_posture()["run_egress"] != egress_doors.OPEN_POSTURE


def test_unchanged_files_are_served_from_the_cache(cfg, monkeypatch):
    _write(cfg / "build_manifest.json", _manifest("island"), 0)
    egress_doors.run_posture()
    calls = []
    real = build_manifest.read_manifest
    monkeypatch.setattr(build_manifest, "read_manifest",
                        lambda *a, **k: calls.append(1) or real(*a, **k))
    egress_doors.run_posture()
    egress_doors.run_posture()
    assert calls == []


def test_a_door_switch_in_config_follows_the_file_both_ways(cfg):
    _write(cfg / "build_manifest.json", _manifest(egress_doors.OPEN_POSTURE), 0)
    _write(cfg / "puffpai_config.json", {"egress_doors": {"weather": True}}, 0)
    assert egress_doors._raw_config()["egress_doors"]["weather"] is True  # noqa: SLF001
    _write(cfg / "puffpai_config.json", {"egress_doors": {"weather": False}}, 1)
    assert egress_doors._raw_config()["egress_doors"]["weather"] is False  # noqa: SLF001
    _write(cfg / "puffpai_config.json", {"egress_doors": {"weather": True}}, 2)
    assert egress_doors._raw_config()["egress_doors"]["weather"] is True  # noqa: SLF001


def test_a_config_that_turns_unreadable_shuts_every_door(cfg):
    _write(cfg / "puffpai_config.json", {"egress_doors": {"weather": True}}, 0)
    assert egress_doors._raw_config()  # noqa: SLF001
    (cfg / "puffpai_config.json").write_text("{not json", encoding="utf-8")
    st = os.stat(cfg / "puffpai_config.json")
    os.utime(cfg / "puffpai_config.json", ns=(st.st_atime_ns, st.st_mtime_ns + 5 * 10**9))
    assert egress_doors._raw_config() == {}  # noqa: SLF001
