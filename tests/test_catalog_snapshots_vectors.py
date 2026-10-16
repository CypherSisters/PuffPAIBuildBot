"""Shipped snapshots and their vectors. No network.

* every snapshot in ``config/catalog_snapshots/`` (and the seed list) has a
  ``.vec.npz`` whose ids are the ids its adapter gives its finds, whose
  ``snapshot_sha256`` matches the snapshot, and whose vectors are unit length;
* a vector file made for another version of its snapshot is refused;
* a Windows checkout (CRLF line endings) keeps the same snapshot hash;
* the snapshot builder is deterministic and reads YAML only at build time;
* the awesome-selfhosted snapshot answers "is this listed" with every licence joined.
"""
from __future__ import annotations

import gzip
import io
import json
import os
import shutil
import tarfile

import numpy as np
import pytest

from puffpai.catalogs import searchers as S
from puffpai.catalogs import snapshot_build_code as build
from puffpai.catalogs import where_to_look as w
from puffpai.catalogs.searchers import awesome_selfhosted as asd

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
SNAP_DIR = os.path.join(ROOT, "config", "catalog_snapshots")


def _snapshot_files() -> list[str]:
    return sorted(n for n in os.listdir(SNAP_DIR) if not n.endswith(".vec.npz"))


def _stem(name: str) -> str:
    return name[:-len(".json.gz")] if name.endswith(".json.gz") else name[:-len(".json")]


def test_every_snapshot_is_known_and_has_vectors():
    stems = {_stem(n) for n in _snapshot_files()} | {"api_seeds"}
    assert stems == set(S.SNAPSHOT_ROWS)
    for stem in stems:
        assert os.path.isfile(os.path.join(SNAP_DIR, stem + ".vec.npz")), stem


@pytest.mark.parametrize("stem", sorted(S.SNAPSHOT_ROWS))
def test_vectors_match_their_snapshot_and_adapter_ids(stem):
    got = w.snapshot_vectors(stem)
    assert got is not None, stem
    ids, vecs = got
    assert ids == [rid for rid, _row in S.snapshot_rows(stem)]
    assert vecs.dtype == np.float16 and vecs.shape == (len(ids), w.VEC_DIM)
    norms = np.linalg.norm(vecs.astype(np.float32), axis=1)
    assert np.all(np.abs(norms - 1.0) < 1e-2)
    with np.load(os.path.join(SNAP_DIR, stem + ".vec.npz"), allow_pickle=False) as z:
        meta = json.loads(str(z["meta"]))
    assert meta == {"model": "all-MiniLM-L6-v2", "archive_sha256": w.VEC_ARCHIVE_SHA256,
                    "text_shape": "candidate_v1",
                    "snapshot_sha256": w.snapshot_sha256(w.snapshot_file(stem))}


def _copy_config(tmp_path, monkeypatch, stem: str):
    cfg = tmp_path / "config"
    (cfg / "catalog_snapshots").mkdir(parents=True)
    snap = w.snapshot_file(stem)
    shutil.copy(snap, cfg / "catalog_snapshots" / os.path.basename(snap))
    shutil.copy(os.path.join(SNAP_DIR, stem + ".vec.npz"),
                cfg / "catalog_snapshots" / (stem + ".vec.npz"))
    monkeypatch.setattr(w, "_config_dir", lambda: str(cfg))
    w._VEC_CACHE.clear()
    return cfg / "catalog_snapshots" / os.path.basename(snap)


def test_a_vector_file_for_another_version_is_refused(tmp_path, monkeypatch):
    snap = _copy_config(tmp_path, monkeypatch, "looks_shadcn_2026-09-24")
    assert w.snapshot_vectors("looks_shadcn_2026-09-24") is not None
    snap.write_text(snap.read_text(encoding="utf-8").replace("shadcn", "shadcN", 1),
                    encoding="utf-8")
    w._VEC_CACHE.clear()
    assert w.snapshot_vectors("looks_shadcn_2026-09-24") is None


def test_a_windows_checkout_keeps_the_same_hash(tmp_path, monkeypatch):
    snap = _copy_config(tmp_path, monkeypatch, "looks_shadcn_2026-09-24")
    raw = snap.read_bytes().replace(b"\r\n", b"\n")
    snap.write_bytes(raw)
    lf = w.snapshot_sha256(str(snap))
    snap.write_bytes(raw.replace(b"\n", b"\r\n"))
    w._VEC_CACHE.clear()
    assert w.snapshot_sha256(str(snap)) == lf
    assert w.snapshot_vectors("looks_shadcn_2026-09-24") is not None


def test_bad_stems_are_refused():
    for stem in ("../keys", "CON", "nul.json", "", "a/b", "x" * 200):
        assert w.snapshot_vectors(stem) is None


def _tiny_asd_tar(tmp_path) -> str:
    files = {
        "asd-main/software/seat-chart.yml": "name: Seat Chart\nwebsite_url: https://example.org/\n"
        "description: Plan the tables.\nlicenses:\n  - MIT\n  - GPL-3.0\nplatforms:\n  - Nodejs\n"
        "tags:\n  - Games\nsource_code_url: https://github.com/someone/seat-chart\n"
        "stargazers_count: 12\nupdated_at: '2026-01-02'\n",
        "asd-main/software/closed-thing.yml": "name: Closed Thing\nwebsite_url: https://c.example\n"
        "description: Not free.\nlicenses:\n  - BUSL-1.1\nplatforms: []\ntags:\n  - Games\n"
        "source_code_url: https://codeberg.org/who/closed\n",
        "asd-main/tags/games.yml": "name: Games\ndescription: Games.\n",
        "asd-main/licenses.yml": "- identifier: MIT\n  name: MIT\n",
        "asd-main/licenses-nonfree.yml": "- identifier: BUSL-1.1\n  name: Business Source\n",
        "asd-main/../escape.yml": "name: nope\n",
    }
    path = str(tmp_path / "asd.tar.gz")
    with tarfile.open(path, "w:gz", format=tarfile.PAX_FORMAT,
                      pax_headers={"comment": "a" * 40}) as tar:
        for name, text in files.items():
            data = text.encode("utf-8")
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return path


def test_the_awesome_selfhosted_build_is_deterministic_and_safe(tmp_path):
    src = _tiny_asd_tar(tmp_path)
    a, b = str(tmp_path / "a.json.gz"), str(tmp_path / "b.json.gz")
    snap = build.build_awesome_selfhosted(src, a, generated="2026-09-27")
    build.build_awesome_selfhosted(src, b, generated="2026-09-27")
    assert open(a, "rb").read() == open(b, "rb").read()
    assert snap["source_commit"] == "a" * 40 and snap["license"] == "CC-BY-SA-3.0"
    assert "CC BY-SA 3.0" in snap["attribution"]
    names = {r["name"] for r in snap["rows"]}
    assert names == {"Seat Chart", "Closed Thing"}                 # the ../ member is never read
    closed = next(r for r in snap["rows"] if r["name"] == "Closed Thing")
    assert closed["nonfree"] is True


def test_the_runtime_never_imports_yaml():
    for mod in ("where_to_look.py", "searchers/awesome_selfhosted.py", "searchers/hacs.py",
                "searchers/__init__.py"):
        with open(os.path.join(ROOT, "src", "puffpai", "catalogs", mod), encoding="utf-8") as fh:
            assert "import yaml" not in fh.read(), mod


def test_listed_joins_every_licence_and_refuses_non_free(tmp_path, monkeypatch):
    out = tmp_path / "config" / "catalog_snapshots"
    out.mkdir(parents=True)
    build.build_awesome_selfhosted(_tiny_asd_tar(tmp_path), str(out / (asd.STEM + ".json.gz")),
                                   generated="2026-09-27")
    monkeypatch.setattr(w, "_config_dir", lambda: str(tmp_path / "config"))
    asd._CACHE["key"] = None
    row = asd.listed("https://github.com/Someone/Seat-Chart/")
    assert row["license"] == "MIT AND GPL-3.0"
    assert w.license_words(row["license"]).startswith("Free to use and change; if you share")
    assert asd.listed("https://codeberg.org/who/closed")["license"] is None
    assert asd.listed("https://github.com/nobody/else") is None
    assert asd.topics() == {"games"}
    asd._CACHE["key"] = None


def test_the_shipped_awesome_selfhosted_snapshot_header():
    with gzip.open(w.snapshot_file(asd.STEM), "rt", encoding="utf-8") as fh:
        snap = json.load(fh)
    assert snap["schema"] == "awesome_selfhosted_snapshot/v1"
    assert snap["source_commit"] == "c10a71ae91ccc513dc4329578896810c499c4b21"
    assert snap["license"] == "CC-BY-SA-3.0" and len(snap["rows"]) == 1351
