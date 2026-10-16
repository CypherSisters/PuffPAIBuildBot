"""Build the shipped meaning vectors beside every catalog snapshot (a maintainer tool).

For every stem in ``puffpai.catalogs.searchers.SNAPSHOT_ROWS`` this writes
``config/catalog_snapshots/<stem>.vec.npz`` with:

* ``ids``  - the find ids, unicode (the same ids the snapshot's adapter gives its finds);
* ``vecs`` - float16, n x 384, each row L2-normalised;
* ``meta`` - a JSON string: model, the reading helper's archive sha256, the
  text shape (``candidate_v1``) and the snapshot's sha256 (line endings
  made ``\\n`` for plain-text snapshots, see ``where_to_look.snapshot_sha256``).

It uses the pinned local reading helper (all-MiniLM-L6-v2) and never downloads
anything; without the helper it stops. The text embedded for each row is
``code_mode.candidate_text`` (the one function the run time uses too).

Usage::

    py -3.14 tools/build_snapshot_vectors.py [stem ...]

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))

BATCH = 256


def build(stem: str, embedder) -> dict:
    import numpy as np

    from puffpai.catalogs import code_mode, searchers, where_to_look as w

    snap = w.snapshot_file(stem)
    if snap is None:
        raise SystemExit(f"{stem}: no snapshot file")
    rows = searchers.snapshot_rows(stem)
    if not rows:
        raise SystemExit(f"{stem}: no rows")
    ids = [rid for rid, _row in rows]
    texts = [code_mode.candidate_text(row) or rid for rid, row in rows]
    out = np.zeros((len(texts), w.VEC_DIM), dtype=np.float32)
    for start in range(0, len(texts), BATCH):
        got = np.asarray(embedder(texts[start:start + BATCH]), dtype=np.float32)
        if got.shape != (len(texts[start:start + BATCH]), w.VEC_DIM):
            raise SystemExit(f"{stem}: the reading helper gave {got.shape}")
        out[start:start + len(got)] = got
    norms = np.linalg.norm(out, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    vecs = (out / norms).astype(np.float16)
    meta = {"model": w.VEC_MODEL, "archive_sha256": w.VEC_ARCHIVE_SHA256,
            "text_shape": code_mode.CANDIDATE_TEXT_SHAPE,
            "snapshot_sha256": w.snapshot_sha256(snap)}
    path = os.path.join(os.path.dirname(snap) if stem != "api_seeds"
                        else os.path.join(os.path.dirname(os.path.dirname(snap)),
                                          "catalog_snapshots"), stem + ".vec.npz")
    tmp = path + ".tmp.npz"
    np.savez_compressed(tmp, ids=np.array(ids, dtype=str), vecs=vecs,
                        meta=np.array(json.dumps(meta, sort_keys=True)))
    os.replace(tmp, path)
    return {"stem": stem, "rows": len(ids), "bytes": os.path.getsize(path), "path": path}


def main(argv: list[str]) -> int:
    from puffpai.catalogs import searchers
    from puffpai.knowledge import reading_helper

    embedder = reading_helper.get_embedder()
    if embedder is None:
        print("The reading helper is not on this computer; nothing was built.")
        return 1
    stems = argv or list(searchers.SNAPSHOT_ROWS)
    for stem in stems:
        t = time.monotonic()
        got = build(stem, embedder)
        print(f"{got['stem']}: {got['rows']} rows, {got['bytes']} bytes, "
              f"{time.monotonic() - t:.1f} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
