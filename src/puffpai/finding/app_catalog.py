"""The app list: programs people run for themselves, from awesome-selfhosted.

Most of these tasks have entire apps or plugins already available with their
own UIs, so the apps come first: every job is first looked up here, by meaning, before skills, tool servers and data.

Where the rows come from:

* the copy shipped with this build (``config/catalog_snapshots/
  awesome_selfhosted_<date>.json.gz`` with its meaning vectors, made by the
  repo's snapshot tools), so the first run works offline;
* a fresh copy, at most once a week, from the community list
  (github.com/awesome-selfhosted/awesome-selfhosted-data: one YAML file per
  app with name, description, tags, source address, platforms, licences,
  demo and stars). It is downloaded through the Bridge like any code
  project (the ``code_download`` service, only while it is on), turned into
  the same snapshot shape by ``catalogs.snapshot_build_code``, and kept in
  ``<state>/finding/app_catalog/``. Its meaning vectors are made here once,
  with the same reading helper and the same text as the shipped ones.

Nothing personal is ever sent: the download asks for the public list only.

The data is awesome-selfhosted-data (CC BY-SA 3.0); each copy keeps the
attribution and the source commit.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import gzip
import json
import os
import threading
import time
from typing import Any, Optional

from puffpai.logging_config import get_logger

logger = get_logger(__name__)

#: How old the fresh copy may get before a new one is fetched.
REFRESH_EVERY_S = 7 * 24 * 3600
#: Where the list is read from (its default branch, as one archive).
ARCHIVE_HOST = "codeload.github.com"
ARCHIVE_URL = ("https://codeload.github.com/awesome-selfhosted/awesome-selfhosted-data/"
               "tar.gz/refs/heads/master")
ARCHIVE_MAX = 30 * 1024 * 1024
SERVICE = "code_download"
SCHEMA = "awesome_selfhosted_snapshot/v1"

_LOCK = threading.Lock()
_CACHE: dict[str, Any] = {"key": None, "rows": [], "vecs": None}
_REFRESHING = threading.Event()


def folder() -> str:
    from puffpai.config import get_state_dir
    return os.path.join(get_state_dir(), "finding", "app_catalog")


def fresh_path() -> str:
    return os.path.join(folder(), "awesome_selfhosted.json.gz")


def _vec_path() -> str:
    return os.path.join(folder(), "awesome_selfhosted.vec.npy")


def _shipped() -> Any:
    from puffpai.catalogs.searchers import awesome_selfhosted as A
    return A


def _fresh() -> tuple[Optional[tuple], list[dict]]:
    """``(cache key, rows)`` of the fresh copy, or ``(None, [])``."""
    path = fresh_path()
    try:
        key = (path, os.path.getmtime(path))
    except OSError:
        return None, []
    with _LOCK:
        if _CACHE["key"] == key:
            return key, list(_CACHE["rows"])
    try:
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            snap = json.load(fh)
    except (OSError, ValueError):
        return None, []
    if not isinstance(snap, dict) or snap.get("schema") != SCHEMA:
        return None, []
    rows = [r for r in snap.get("rows") or [] if isinstance(r, dict) and r.get("id")]
    if not rows:
        return None, []
    with _LOCK:
        _CACHE.update(key=key, rows=rows, vecs=None)
    return key, rows


def rows() -> list[dict]:
    """Every app row: the fresh copy when there is one, else the shipped one."""
    _key, got = _fresh()
    if got:
        return got
    return _shipped().rows_raw()


def _fresh_vectors(key: tuple, got: list[dict]) -> Any:
    """Unit vectors for the fresh rows (made once, kept beside the copy)."""
    import numpy as np
    with _LOCK:
        if _CACHE["key"] == key and _CACHE["vecs"] is not None:
            return _CACHE["vecs"]
    vecs = None
    try:
        if os.path.getmtime(_vec_path()) >= key[1]:
            vecs = np.load(_vec_path(), allow_pickle=False)
            if vecs.shape[0] != len(got):
                vecs = None
    except (OSError, ValueError):
        vecs = None
    if vecs is None:
        from puffpai.catalogs import code_mode
        from puffpai.knowledge import reading_helper
        embed = reading_helper.get_embedder()
        if embed is None:
            return None
        texts = [code_mode.candidate_text(r) or str(r["id"]) for r in got]
        out = np.asarray(embed(texts), dtype=np.float32)
        norms = np.linalg.norm(out, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        vecs = (out / norms).astype(np.float16)
        try:
            os.makedirs(folder(), exist_ok=True)
            tmp = _vec_path() + ".tmp.npy"
            np.save(tmp, vecs, allow_pickle=False)
            os.replace(tmp, _vec_path())
        except OSError:
            pass
    with _LOCK:
        if _CACHE["key"] == key:
            _CACHE["vecs"] = vecs
    return vecs


def meaning_match(text: str, *, limit: int, min_sim: float) -> list[tuple[float, str]]:
    """``[(cosine, row id)]`` best first: apps whose name, description and
    tags mean what *text* means. [] without the reading helper."""
    from puffpai.catalogs import searchers as CS
    key, got = _fresh()
    if not got:
        return CS.vector_match(_shipped().STEM, text, limit=limit, min_sim=min_sim)
    vecs = _fresh_vectors(key, got)
    vec = CS._embed_one(text)  # noqa: SLF001 - the one cached job embedder
    if vecs is None or vec is None:
        return []
    import numpy as np
    sims = vecs.astype(np.float32) @ vec
    order = np.argsort(-sims)[:max(0, int(limit))]
    return [(float(sims[i]), str(got[i]["id"])) for i in order if sims[i] >= min_sim]


def due(now: Optional[float] = None) -> bool:
    """Is a fresh copy due (none yet, or older than a week)?"""
    now = time.time() if now is None else now
    try:
        return now - os.path.getmtime(fresh_path()) >= REFRESH_EVERY_S
    except OSError:
        return True


def _switch_on() -> bool:
    try:
        from puffpai.security import outside_switches
        return not outside_switches.is_off(SERVICE)
    except Exception:  # noqa: BLE001 - not knowing: not fetched
        return False


def refresh(*, get_archive: Any = None, build: Any = None) -> dict:
    """Fetch the list once and keep it as the fresh copy. ``{"ok", "why"}``.
    Never raises. Only while the ``code_download`` service is on."""
    if get_archive is None and not _switch_on():
        return {"ok": False, "why": "switched_off"}
    try:
        import yaml  # noqa: F401 - the list's own format
    except ImportError:
        return {"ok": False, "why": "no_yaml"}
    if get_archive is None:
        from puffpai import source_fetch
        get_archive = source_fetch.get_archive
    if build is None:
        from puffpai.catalogs import snapshot_build_code
        build = snapshot_build_code.build_awesome_selfhosted
    os.makedirs(folder(), exist_ok=True)
    tar = os.path.join(folder(), "download.tar.gz")
    tmp = fresh_path() + ".tmp"
    try:
        # No fingerprint on purpose: the list changes every week and has no
        # published one. It is data (names, addresses, platforms) read by the
        # snapshot builder, never run; each app found in it is checked by
        # Drawbridge at its own pinned commit before anything is added.
        get_archive(ARCHIVE_URL, allowed_hosts={ARCHIVE_HOST}, max_bytes=ARCHIVE_MAX,
                    dest_path=tar, service=SERVICE, max_redirects=2, expect_sha256=None)
        build(tar, tmp, generated=time.strftime("%Y-%m-%d"))
        os.replace(tmp, fresh_path())
        try:
            os.remove(_vec_path())
        except OSError:
            pass
        return {"ok": True, "why": ""}
    except Exception as exc:  # noqa: BLE001 - the shipped or older copy stays
        logger.info("app list: not refreshed (%s)", type(exc).__name__)
        return {"ok": False, "why": str(getattr(exc, "code", "") or type(exc).__name__)}
    finally:
        for p in (tar, tmp):
            try:
                os.remove(p)
            except OSError:
                pass


def refresh_soon() -> bool:
    """Start a weekly refresh in the background when one is due (at most one
    at a time; a search never waits for it). True when one was started."""
    if not due() or _REFRESHING.is_set() or not _switch_on():
        return False
    _REFRESHING.set()

    def run() -> None:
        try:
            refresh()
        finally:
            _REFRESHING.clear()
    threading.Thread(target=run, name="app-list-refresh", daemon=True).start()
    return True


__all__ = ["ARCHIVE_URL", "REFRESH_EVERY_S", "due", "fresh_path", "meaning_match", "refresh",
           "refresh_soon", "rows"]
