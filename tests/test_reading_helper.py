"""The reading helper is found on this computer or absent; it is never downloaded.

Also the guard's seam: it replaces a PRIVATE chromadb method, so chromadb is
pinned to one version and these tests fail if that method goes away.
"""
from __future__ import annotations

import hashlib
import io
import os
import re
import socket
import sys
import tarfile
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from learning_support import jargon_in  # noqa: E402

from puffpai.knowledge import reading_helper  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def no_helper(tmp_path, monkeypatch):
    """No copy anywhere, and any attempt to open a socket fails the test."""
    monkeypatch.setattr(reading_helper, "chroma_cache_dir", lambda: str(tmp_path / "cache"))
    monkeypatch.setattr(reading_helper, "helper_dir", lambda: str(tmp_path / "own"))

    def no_network(*a, **k):
        raise AssertionError("the reading helper tried to go online")
    monkeypatch.setattr(socket, "create_connection", no_network)
    monkeypatch.setattr(socket.socket, "connect", no_network)
    return tmp_path


def test_the_private_chromadb_seam_still_exists():
    from chromadb.utils.embedding_functions import onnx_mini_lm_l6_v2 as mod
    assert hasattr(mod.ONNXMiniLM_L6_V2, "_download_model_if_not_exists")
    assert hasattr(mod.ONNXMiniLM_L6_V2, "_download")
    assert mod.ONNXMiniLM_L6_V2._MODEL_SHA256 == reading_helper.ARCHIVE_SHA256
    assert mod.ONNXMiniLM_L6_V2.MODEL_DOWNLOAD_URL == reading_helper.ARCHIVE_URL


def test_chromadb_is_pinned_to_one_version():
    import importlib.metadata
    import re

    import chromadb
    pins = [ln.strip() for ln in (ROOT / "requirements.txt").read_text().splitlines()
            if ln.strip().startswith("chromadb")]
    assert len(pins) == 1 and re.fullmatch(r"chromadb==\d+\.\d+\.\d+", pins[0]), pins
    # The chromadb these tests ran on is the pinned one wherever this Python
    # was installed from BuildBot's own requirements (setup.py passes the pin
    # on as written; CI's pip install -e .). A Python set up for another
    # project may hold another chromadb; then the pin is all there is to check.
    try:
        ours = pins[0] in (importlib.metadata.requires("puffpai") or [])
    except importlib.metadata.PackageNotFoundError:
        ours = False
    if ours:
        assert pins == [f"chromadb=={chromadb.__version__}"]


def test_a_missing_helper_never_opens_a_socket(no_helper, monkeypatch):
    assert reading_helper.get_embedder() is None
    assert reading_helper.status()["state"] == "missing"
    assert reading_helper.install_guard()
    from chromadb.utils.embedding_functions import DefaultEmbeddingFunction
    from chromadb.utils.embedding_functions import onnx_mini_lm_l6_v2 as mod
    monkeypatch.setattr(mod.ONNXMiniLM_L6_V2, "DOWNLOAD_PATH", no_helper / "nowhere")
    ef = DefaultEmbeddingFunction()
    with pytest.raises(reading_helper.HelperMissing):
        ef(["how do lizards keep warm"])
    assert not (no_helper / "nowhere").exists(), "not even a folder was made"


def test_nothing_outside_the_helper_builds_chromas_embedder():
    offenders = []
    for path in (ROOT / "src" / "puffpai").rglob("*.py"):
        if path.name == "reading_helper.py":
            continue
        text = path.read_text(encoding="utf-8")
        if re.search(r"DefaultEmbeddingFunction\s*\(|ONNXMiniLM_L6_V2\s*\(", text):
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


def _tar(members: dict[str, bytes], *, symlink: str = "") -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        if symlink:
            info = tarfile.TarInfo(symlink)
            info.type = tarfile.SYMTYPE
            info.linkname = "/etc/passwd"
            tar.addfile(info)
    return buf.getvalue()


def _pinned(monkeypatch, blob: bytes, files: dict[str, bytes]):
    monkeypatch.setattr(reading_helper, "ARCHIVE_SHA256", hashlib.sha256(blob).hexdigest())
    monkeypatch.setattr(reading_helper, "FILES", {
        name: (hashlib.sha256(data).hexdigest(), len(data)) for name, data in files.items()})


def test_a_file_that_does_not_match_its_check_number_is_refused(no_helper):
    bad = no_helper / "fake.tar.gz"
    bad.write_bytes(_tar({"onnx/model.onnx": b"not the model"}))
    out = reading_helper.install_from_file(str(bad))
    assert not out["ok"] and "check number" in out["reason"]
    assert not os.path.exists(os.path.join(reading_helper.helper_dir(), "onnx"))


def test_paths_that_climb_out_are_never_written(no_helper, monkeypatch):
    files = {"model.onnx": b"m", "vocab.txt": b"v"}
    blob = _tar({"../../escape.txt": b"x", "onnx/../../../evil": b"x",
                 "onnx/model.onnx": b"m", "onnx/vocab.txt": b"v"})
    _pinned(monkeypatch, blob, files)
    archive = no_helper / "a.tar.gz"
    archive.write_bytes(blob)
    out = reading_helper.install_from_file(str(archive))
    assert out["ok"], out
    assert not (no_helper / "escape.txt").exists()
    assert not (no_helper / "evil").exists()
    assert sorted(os.listdir(os.path.join(reading_helper.helper_dir(), "onnx"))) == \
        ["model.onnx", "vocab.txt"]


def test_a_link_inside_the_archive_is_refused(no_helper, monkeypatch):
    files = {"model.onnx": b"m", "config.json": b"c"}
    blob = _tar({"onnx/model.onnx": b"m"}, symlink="onnx/config.json")
    _pinned(monkeypatch, blob, files)
    archive = no_helper / "b.tar.gz"
    archive.write_bytes(blob)
    out = reading_helper.install_from_file(str(archive))
    assert not out["ok"]
    assert not os.path.exists(os.path.join(reading_helper.helper_dir(), "onnx"))


def test_a_tampered_unpacked_file_is_not_ready(no_helper, monkeypatch):
    files = {"model.onnx": b"m"}
    monkeypatch.setattr(reading_helper, "FILES", {
        "model.onnx": (hashlib.sha256(b"m").hexdigest(), 1)})
    folder = no_helper / "own" / "onnx"
    folder.mkdir(parents=True)
    (folder / "model.onnx").write_bytes(b"x")  # same size, wrong bytes
    assert reading_helper.status()["state"] == "bad"
    assert reading_helper.locate() is None
    (folder / "model.onnx").write_bytes(files["model.onnx"])
    os.utime(folder / "model.onnx", ns=(1, 1))
    assert reading_helper.locate() == str(no_helper / "own")


def test_the_outside_row_has_the_shared_shape_and_plain_words(no_helper):
    row = reading_helper.outside_row()
    # The shape the /outside page reads (outside_services._reading_helper_row).
    assert set(row) == {"id", "name", "for", "what_leaves", "state", "can_switch",
                        "state_line", "websites", "why_cannot_switch"}
    assert row["id"] == "reading_helper" and row["can_switch"] is False
    assert row["state"].startswith("Not on this computer")
    for key in ("name", "for", "what_leaves", "state", "state_line",
                "why_cannot_switch"):
        assert not jargon_in(row[key]), row[key]


@pytest.mark.skipif(reading_helper.locate() is None,
                    reason="no reading helper on this computer")
def test_the_real_helper_reads_offline_with_half_the_processor(monkeypatch):
    def no_network(*a, **k):
        raise AssertionError("went online")
    monkeypatch.setattr(socket, "create_connection", no_network)
    ef = reading_helper.get_embedder(threads=reading_helper.cpu_threads(0.5))
    vectors = ef(["how do lizards keep warm", "reptiles bask in the sun"])
    assert len(vectors) == 2 and len(vectors[0]) == 384
    assert ef.model.get_session_options().intra_op_num_threads == \
        reading_helper.cpu_threads(0.5)
