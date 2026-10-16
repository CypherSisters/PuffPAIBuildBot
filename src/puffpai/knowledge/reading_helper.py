"""The reading helper: the small model that lets the bot match questions
worded differently from its books (all-MiniLM-L6-v2, 83 MB, Apache 2.0).

Chroma's own embedder downloads this model from the internet the first time
it is used, silently, into ``~/.cache/chroma``. BuildBot never lets it: the
helper is found on this computer or it is absent.

* :func:`locate` finds a complete, checked copy: BuildBot's own folder
  (``<data>/models/reading_helper``, filled by the installer's
  "Getting the reading helper" step) first, then Chroma's existing cache.
  Every file is checked against the SHA-256 pinned below.
* :func:`get_embedder` is the only place an embedder is built. It returns
  ``None`` when the helper is absent; callers carry on without it (books
  and uploads still answer through their word look-up).
* :func:`install_guard` swaps Chroma's private download method for one that
  refuses, so no other code path (a collection rebuilding its default
  embedder, a desktop view) can fetch the model behind the owner's back.
  It relies on a private Chroma method, which is why chromadb is pinned to
  one exact version and ``tests/test_reading_helper.py`` fails if the method
  disappears.
* :func:`install_from_file` takes the downloaded archive, checks its SHA-256
  and unpacks only the six known files (no paths, no links).

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import hashlib
import logging
import os
import shutil
import tarfile
import tempfile
import threading
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

HELPER_NAME = "all-MiniLM-L6-v2"
#: The one address the installer fetches from, and the archive's pinned hash.
ARCHIVE_URL = ("https://chroma-onnx-models.s3.amazonaws.com/"
               "all-MiniLM-L6-v2/onnx.tar.gz")
ARCHIVE_SHA256 = "913d7300ceae3b2dbc2c50d1de4baacab4be7b9380491c27fab7418616a16ec3"
ARCHIVE_BYTES = 83_178_821
#: Refuse an archive bigger than this before reading it.
MAX_ARCHIVE_BYTES = 90 * 1024 * 1024
LICENSE = "Apache-2.0"
SIZE_WORDS = "83 MB"

#: Every file the helper needs: name -> (sha256, size). Nothing else is unpacked.
FILES: dict[str, tuple[str, int]] = {
    "config.json": ("b567c7d5a55b636c95186aaf993f9a8920842b7e05a9e703e68b23cab2c3a670", 650),
    "model.onnx": ("4f148ba8ae9c2c7fbee4af2b132db8d06c6a6545b47fc83bbb98c3d22b8393e6", 90_387_606),
    "special_tokens_map.json": ("b6d346be366a7d1d48332dbc9fdf3bf8960b5d879522b7799ddba59e76237ee3", 125),
    "tokenizer.json": ("da0e79933b9ed51798a3ae27893d3c5fa4a201126cef75586296df9b4d2c62a0", 711_661),
    "tokenizer_config.json": ("7702051bbc4953b94d47fa1d61b42ed4cbb3c71b501a8dd7183a823f8bea1f20", 518),
    "vocab.txt": ("07eced375cec144d27c900241f3e339478dec958f92fddbc551f295c992038a3", 231_508),
}
SUBFOLDER = "onnx"


class HelperMissing(RuntimeError):
    """The reading helper is not on this computer, and will not be fetched."""

    def __init__(self, message: str = "") -> None:
        super().__init__(message or "The reading helper is not on this computer.")


# -- where it lives --------------------------------------------------------------

def helper_dir() -> str:
    """BuildBot's own copy: ``<data>/models/reading_helper``."""
    try:
        from puffpai.config import get_config
        base = get_config().DATA_DIR
    except Exception:  # noqa: BLE001
        base = os.path.abspath(".")
    return os.path.join(base, "models", "reading_helper")


def chroma_cache_dir() -> str:
    """Where Chroma keeps it when an earlier run already had it."""
    return str(Path.home() / ".cache" / "chroma" / "onnx_models" / HELPER_NAME)


_checked: dict[tuple, bool] = {}
_checked_lock = threading.Lock()


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _folder_state(folder: str) -> str:
    """'ready' (every file there and matching its pin), 'missing' or 'bad'."""
    files = os.path.join(folder, SUBFOLDER)
    stats = {}
    for name, (_sha, size) in FILES.items():
        try:
            st = os.stat(os.path.join(files, name))
        except OSError:
            return "missing"
        if st.st_size != size:
            return "bad"
        stats[name] = (st.st_size, st.st_mtime_ns)
    key = (os.path.normcase(os.path.abspath(files)), tuple(sorted(stats.items())))
    with _checked_lock:
        known = _checked.get(key)
    if known is None:
        known = all(_sha256(os.path.join(files, name)) == sha
                    for name, (sha, _size) in FILES.items())
        with _checked_lock:
            _checked[key] = known
    return "ready" if known else "bad"


def locate() -> Optional[str]:
    """The folder of a complete, checked helper, or None. Never downloads."""
    for folder in (helper_dir(), chroma_cache_dir()):
        try:
            if _folder_state(folder) == "ready":
                return folder
        except OSError:
            continue
    return None


def status() -> dict[str, Any]:
    """``{"state": "ready"|"missing"|"bad", "where": "own"|"cache"|None}``."""
    worst = "missing"
    for where, folder in (("own", helper_dir()), ("cache", chroma_cache_dir())):
        try:
            state = _folder_state(folder)
        except OSError:
            state = "missing"
        if state == "ready":
            return {"state": "ready", "where": where, "size_bytes": ARCHIVE_BYTES}
        if state == "bad":
            worst = "bad"
    return {"state": worst, "where": None, "size_bytes": ARCHIVE_BYTES}


def is_ready() -> bool:
    return locate() is not None


# -- installing from the downloaded archive ----------------------------------------

def install_from_file(archive: str) -> dict[str, Any]:
    """Check *archive* against the pin and unpack the six known files.

    Returns ``{"ok": bool, "reason": plain words}``. Nothing is written into
    BuildBot's folder unless every file matched its own pin.
    """
    try:
        size = os.path.getsize(archive)
    except OSError:
        return {"ok": False, "reason": "That file is not there."}
    if size > MAX_ARCHIVE_BYTES:
        return {"ok": False, "reason": "That file is too big to be the reading helper."}
    if _sha256(archive) != ARCHIVE_SHA256:
        return {"ok": False, "reason": ("That file is not the reading helper: its "
                                        "check number does not match. Nothing was "
                                        "changed.")}
    target = helper_dir()
    os.makedirs(target, exist_ok=True)
    staging = tempfile.mkdtemp(prefix=".helper-", dir=target)
    try:
        wanted = {f"{SUBFOLDER}/{name}": name for name in FILES}
        got: set[str] = set()
        with tarfile.open(archive, mode="r:gz") as tar:
            for member in tar:
                name = member.name.replace("\\", "/").lstrip("./")
                if name not in wanted:
                    continue  # directories and anything unexpected are skipped
                if not member.isfile():
                    return {"ok": False, "reason": "That file is not the reading helper."}
                expected_sha, expected_size = FILES[wanted[name]]
                if member.size != expected_size:
                    return {"ok": False, "reason": "That file is not the reading helper."}
                src = tar.extractfile(member)
                if src is None:
                    return {"ok": False, "reason": "That file is not the reading helper."}
                data_h = hashlib.sha256()
                out_path = os.path.join(staging, wanted[name])
                with src, open(out_path, "wb") as out:
                    for block in iter(lambda: src.read(1 << 20), b""):
                        data_h.update(block)
                        out.write(block)
                if data_h.hexdigest() != expected_sha:
                    return {"ok": False, "reason": "That file is not the reading helper."}
                got.add(wanted[name])
        if got != set(FILES):
            return {"ok": False, "reason": "That file is missing parts of the reading helper."}
        final = os.path.join(target, SUBFOLDER)
        if os.path.isdir(final):
            shutil.rmtree(final, ignore_errors=True)
        os.replace(staging, final)
        staging = ""
    except (OSError, tarfile.TarError, EOFError) as exc:
        logger.warning("reading helper archive unreadable: %s", exc)
        return {"ok": False, "reason": "That file could not be read. Nothing was changed."}
    finally:
        if staging:
            shutil.rmtree(staging, ignore_errors=True)
    return {"ok": True, "reason": "The reading helper is ready."}


# -- the embedder ------------------------------------------------------------------

def cpu_threads(share: float = 1.0) -> int:
    """How many threads a *share* of this computer's processor is (at least 1)."""
    total = os.cpu_count() or 2
    return max(1, int(total * max(0.05, min(1.0, float(share)))))


_embedders: dict[tuple, Any] = {}
_embedders_lock = threading.Lock()
_local_class: Any = None


def _local_minilm_class() -> Any:
    """Chroma's MiniLM embedder that reads one folder and never downloads."""
    global _local_class
    if _local_class is not None:
        return _local_class
    from functools import cached_property

    from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2

    class LocalMiniLM(ONNXMiniLM_L6_V2):
        def __init__(self, folder: str, threads: int = 0) -> None:
            super().__init__(preferred_providers=["CPUExecutionProvider"])
            self.DOWNLOAD_PATH = folder
            self._threads = int(threads or 0)

        def _download_model_if_not_exists(self) -> None:
            if _folder_state(self.DOWNLOAD_PATH) != "ready":
                raise HelperMissing()

        def _download(self, url: str, fname: str, chunk_size: int = 1024) -> None:
            raise HelperMissing()

        @cached_property
        def model(self) -> Any:
            so = self.ort.SessionOptions()
            so.log_severity_level = 3
            so.graph_optimization_level = self.ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            if self._threads:
                so.intra_op_num_threads = self._threads
                so.inter_op_num_threads = 1
            return self.ort.InferenceSession(
                os.path.join(self.DOWNLOAD_PATH, SUBFOLDER, "model.onnx"),
                providers=["CPUExecutionProvider"], sess_options=so)

    _local_class = LocalMiniLM
    return LocalMiniLM


def get_embedder(threads: int = 0) -> Any:
    """The reading helper as a Chroma embedding function, or None when absent.

    ``threads`` caps the processor threads it may use (0 = no cap).
    """
    folder = locate()
    if folder is None:
        return None
    install_guard()
    key = (folder, int(threads or 0))
    with _embedders_lock:
        found = _embedders.get(key)
        if found is None:
            found = _local_minilm_class()(folder, threads)
            _embedders[key] = found
    return found


_guard_done = False


def install_guard() -> bool:
    """Make Chroma's own MiniLM refuse to download. Returns True when in place."""
    global _guard_done
    if _guard_done:
        return True
    try:
        from chromadb.utils.embedding_functions import onnx_mini_lm_l6_v2 as onnx_mod
    except Exception:  # noqa: BLE001 - no chromadb, nothing to guard
        return False
    cls = onnx_mod.ONNXMiniLM_L6_V2

    def _no_download(self: Any) -> None:
        here = str(getattr(self, "DOWNLOAD_PATH", "") or "")
        if here and all(os.path.exists(os.path.join(here, SUBFOLDER, name))
                        for name in FILES):
            return
        folder = locate()
        if folder is None:
            raise HelperMissing()
        self.DOWNLOAD_PATH = folder

    def _refuse(self: Any, *args: Any, **kwargs: Any) -> None:
        raise HelperMissing()

    cls._download_model_if_not_exists = _no_download
    cls._download = _refuse
    _guard_done = True
    return True


# -- for the list of outside services ------------------------------------------------

def outside_row() -> dict[str, Any]:
    """One row for the owner's list of things that come from outside."""
    ready = is_ready()
    return {
        "id": "reading_helper",
        "name": "Reading helper",
        "for": ("Lets your bot match questions worded differently from the "
                "words in its books."),
        "what_leaves": ("One plain request for the file, once, when BuildBot is "
                        "set up. Nothing about you or your household."),
        "state": ("On this computer" if ready else
                  "Not on this computer. Books still answer questions that use "
                  "their own words."),
        "state_line": ("On this computer." if ready else
                       "Not on this computer yet."),
        "websites": [ARCHIVE_URL.split("/")[2]],
        "can_switch": False,
        "why_cannot_switch": ("Only the installer fetches it, once, and checks "
                              "it is the exact file expected. BuildBot itself "
                              "never downloads it."),
    }


__all__ = ["ARCHIVE_URL", "ARCHIVE_SHA256", "ARCHIVE_BYTES", "FILES", "LICENSE",
           "SIZE_WORDS", "HelperMissing", "helper_dir", "chroma_cache_dir",
           "locate", "status", "is_ready", "install_from_file", "cpu_threads",
           "get_embedder", "install_guard", "outside_row"]
