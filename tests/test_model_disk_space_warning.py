"""BB-DSK-01 - say the disk cannot hold the model BEFORE the download starts.

The wizard states a model's download size and asks first, but nothing checked
whether the volume it lands on could hold it. On a small box the honest failure
- "you have 3 GB free and this is a 5 GB model" - arrived from Ollama, at 80%,
with a partially-filled drive, instead of from the wizard, before the yes/no.

The deliberate shape of the fix: it WARNS, never refuses. The catalog size is
an estimate and the volume we found may not be the one the owner is looking at;
a wizard that blocks a download over its own arithmetic is worse than one that
lets someone who knows their disk proceed.

Run:
    pytest tests/test_model_disk_space_warning.py
"""
from __future__ import annotations

import os
import shutil
from collections import namedtuple

import pytest

from puffpai import brain_health

_Usage = namedtuple("_Usage", "total used free")
_GB = 1024 ** 3


@pytest.fixture
def fake_disk(monkeypatch):
    """Pin free space to whatever a test asks for, on any path."""
    def _set(free_gb: float):
        monkeypatch.setattr(
            shutil, "disk_usage",
            lambda _p: _Usage(100 * _GB, 0, int(free_gb * _GB)))
    return _set


@pytest.fixture(autouse=True)
def local_store(monkeypatch, tmp_path):
    """A real, existing directory to stand in for ~/.ollama/models."""
    monkeypatch.setenv("OLLAMA_MODELS", str(tmp_path))


# --- the warning fires when it should -------------------------------------


def test_a_5_gb_model_onto_3_gb_free_warns(fake_disk):
    fake_disk(3.0)
    msg = brain_health.disk_headroom(5, "http://localhost:11434")
    assert msg
    assert "3.0 GB free" in msg
    assert "6.0 GB" in msg          # 5 x the 1.2 headroom
    assert "run out" in msg


def test_the_headroom_margin_is_what_makes_a_tight_fit_warn(fake_disk):
    # 5.5 GB free holds a 5 GB model exactly, but not the unpack margin.
    fake_disk(5.5)
    assert brain_health.disk_headroom(5, "http://localhost:11434")


def test_ample_free_space_says_nothing(fake_disk):
    fake_disk(80.0)
    assert brain_health.disk_headroom(5, "http://localhost:11434") == ""


def test_exactly_enough_says_nothing(fake_disk):
    fake_disk(6.0)
    assert brain_health.disk_headroom(5, "http://localhost:11434") == ""


# --- and stays quiet where it cannot honestly speak ------------------------


def test_an_unknown_model_size_produces_no_warning(fake_disk):
    fake_disk(0.1)
    for size in (None, 0, "", "not a number"):
        assert brain_health.disk_headroom(size, "http://localhost:11434") == ""


def test_a_remote_ollama_is_not_warned_about(fake_disk):
    """Someone else's server fills someone else's disk."""
    fake_disk(0.1)
    assert brain_health.disk_headroom(50, "http://192.168.1.50:11434") == ""


def test_127_0_0_1_counts_as_this_box(fake_disk):
    fake_disk(0.1)
    assert brain_health.disk_headroom(50, "http://127.0.0.1:11434")


def test_an_unreadable_volume_does_not_block_a_download(monkeypatch):
    def _boom(_p):
        raise OSError("device not ready")
    monkeypatch.setattr(shutil, "disk_usage", _boom)
    assert brain_health.disk_headroom(50, "http://localhost:11434") == ""


def test_a_model_store_that_does_not_exist_yet_checks_its_parent_volume(
        monkeypatch, tmp_path, fake_disk):
    """~/.ollama/models is not created until the first pull."""
    missing = os.path.join(str(tmp_path), "nope", "models")
    monkeypatch.setenv("OLLAMA_MODELS", missing)
    fake_disk(1.0)
    msg = brain_health.disk_headroom(5, "http://localhost:11434")
    assert msg
    assert str(tmp_path) in msg


# --- the store location ----------------------------------------------------


def test_ollama_models_env_var_wins(monkeypatch):
    monkeypatch.setenv("OLLAMA_MODELS", "D:/models")
    assert brain_health.model_store_dir("http://localhost:11434") == "D:/models"


def test_the_default_store_is_under_the_home_dir(monkeypatch):
    monkeypatch.delenv("OLLAMA_MODELS", raising=False)
    store = brain_health.model_store_dir("http://localhost:11434")
    assert store.endswith(os.path.join(".ollama", "models"))


def test_a_remote_url_has_no_local_store(monkeypatch):
    monkeypatch.setenv("OLLAMA_MODELS", "D:/models")
    assert brain_health.model_store_dir("http://10.0.0.9:11434") == ""
