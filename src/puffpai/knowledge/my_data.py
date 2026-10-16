"""The owner's data folder, her linked folders, and the light watcher over both.

One folder, chosen by the owner (``knowledge.data_dir`` in the main config),
holds what BuildBot downloads for her (books, offline libraries) AND what she
adds herself (uploads land in ``<data_dir>/My files/``). Linked folders are
folders she already has (``knowledge.linked_folders``): they are read where
they are, never copied, moved or changed.

``knowledge.data_dir`` is the one setting. ``knowledge.zim_dir`` is written
with the same value so older readers (the book indexer at start-up, the CLI)
keep finding packs; when ``data_dir`` is empty, ``zim_dir`` (then the default
pack folder) is used, so an existing install keeps working as it was.
Changing the folder never deletes the old one: it is kept in
``knowledge.previous_dirs`` and its files are still read.

The watcher is a polling thread (every :data:`SCAN_SECONDS`, plus once at
start-up, plus a poke after an upload or a new link): a cheap walk comparing
each file's change time and size with ``<state>/knowledge/my_data.json``.
New or changed files go through the same path as an upload
(:func:`puffpai.ingestion.ingest_file`: keys and personal details checked,
held in the vault, a redacted copy indexed in the uploads search); files that
are gone leave the index. Hidden and system files, links, ``.zim`` packs and
downloaded book folders (the book indexer's) are skipped; files over
``knowledge.max_file_mb`` are skipped and counted. ``knowledge.auto_index =
false`` stops it.

A file with a key or a personal detail found while the vault is closed is not
refused and not indexed: its values are sealed into the vault's write-only
inbox, it is marked ``waiting``, and the first scan after the vault is opened
finishes it (redact, index, vault). Logs name files and counts only.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import stat
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

MY_FILES = "My files"
SCAN_SECONDS = 45
DEFAULT_MAX_FILE_MB = 25
#: At most this many files are looked at per folder per scan.
MAX_FILES_PER_FOLDER = 20_000

#: Endings the watcher reads. Pictures are left to an upload (reading text
#: from every photo in a linked folder would keep the computer busy for hours).
WATCH_EXTS = frozenset({
    ".txt", ".md", ".markdown", ".pdf", ".docx", ".csv", ".xlsx", ".xls",
    ".json", ".yaml", ".yml", ".ini", ".cfg", ".conf", ".toml", ".log", ".rst",
})
_SKIP_DIRS = frozenset({"$recycle.bin", "system volume information", "node_modules",
                        "__pycache__", "host_apps"})
#: BuildBot's own sidecar files next to downloads and apps, never her upload.
_OWN_SUFFIXES = (".provenance.json",)
_OWN_PREFIXES = (".bb-local.",)
_HIDDEN_ATTRS = 0x2 | 0x4  # FILE_ATTRIBUTE_HIDDEN | FILE_ATTRIBUTE_SYSTEM

INDEXED, WAITING, TOO_BIG, UNREADABLE, IN_VAULT = (
    "indexed", "waiting", "too_big", "unreadable", "in_vault")

WAITING_LINE = ("{n} waiting for the vault: open your vault with your PIN and "
                "they are added by themselves.")
MOVE_NOTE = ("Your old folder is kept as it was, and its files are still "
             "found. Nothing was moved or deleted.")

_lock = threading.RLock()
_wake = threading.Event()
_watcher: dict[str, Any] = {"thread": None, "indexing": 0}


# ---------------------------------------------------------------------------
# settings
# ---------------------------------------------------------------------------

def _cfg():
    from puffpai.config import get_config
    return get_config()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _canon(path: str) -> str:
    return os.path.normcase(os.path.realpath(os.path.abspath(path)))


def _inside_or_equal(child: str, parent: str) -> bool:
    if child == parent:
        return True
    try:
        return os.path.commonpath([child, parent]) == parent
    except ValueError:  # different drives
        return False


def data_dir() -> str:
    """The owner's data folder: the setting, else the old pack folder, else
    the default under the data dir."""
    k = _cfg().knowledge
    folder = str(getattr(k, "data_dir", "") or "").strip()
    if folder:
        return folder
    folder = str(getattr(k, "zim_dir", "") or "").strip()
    if folder:
        return folder
    from puffpai import setup_ops
    return setup_ops.default_pack_dir()


def my_files_dir(*, create: bool = False) -> str:
    folder = os.path.join(data_dir(), MY_FILES)
    if create:
        os.makedirs(folder, exist_ok=True)
    return folder


def previous_dirs() -> list[str]:
    raw = getattr(_cfg().knowledge, "previous_dirs", None) or []
    return [str(p) for p in raw if isinstance(p, str) and p.strip()]


def linked_folders() -> list[dict[str, Any]]:
    raw = getattr(_cfg().knowledge, "linked_folders", None) or []
    return [dict(r) for r in raw
            if isinstance(r, dict) and r.get("id") and r.get("path")]


def max_file_bytes() -> int:
    try:
        mb = int(getattr(_cfg().knowledge, "max_file_mb", DEFAULT_MAX_FILE_MB)
                 or DEFAULT_MAX_FILE_MB)
    except (TypeError, ValueError):
        mb = DEFAULT_MAX_FILE_MB
    return max(1, mb) * 1024 * 1024


def free_space_line(folder: str) -> str:
    probe = folder
    while probe and not os.path.exists(probe):
        parent = os.path.dirname(probe)
        if parent == probe:
            break
        probe = parent
    try:
        free = shutil.disk_usage(probe or ".").free
    except OSError:
        return "Free space there: unknown."
    return f"Free space there: {free / 1024 ** 3:.1f} GB."


# ---------------------------------------------------------------------------
# which folders are obviously wrong
# ---------------------------------------------------------------------------

def _wrong_places() -> list[tuple[str, str]]:
    """``(canonical folder, plain reason)`` for places never used here."""
    from puffpai.config import get_state_dir
    from puffpai.security import owner_folders
    out: list[tuple[str, str]] = []
    try:
        vd = owner_folders.vault_dir()
        out += [(_canon(vd), "That is your vault's folder."),
                (_canon(vd + ".inbox"), "That is your vault's folder.")]
    except Exception:  # noqa: BLE001
        pass
    out.append((_canon(get_state_dir()), "That is BuildBot's own settings folder."))
    env = os.environ.get
    home = (env("USERPROFILE") if os.name == "nt" else None) or os.path.expanduser("~")
    for p in (os.path.join(home, ".ssh"), os.path.join(home, ".gnupg")):
        out.append((_canon(p), "That folder holds your sign-in keys."))
    if os.name == "nt":
        for p in (env("SystemRoot"), env("ProgramFiles"), env("ProgramFiles(x86)"),
                  env("ProgramData")):
            if p:
                out.append((_canon(p), "That is a system folder."))
    else:
        for p in ("/etc", "/usr", "/bin", "/sbin", "/lib", "/boot", "/proc",
                  "/sys", "/dev", "/run", "/var/lib"):
            out.append((_canon(p), "That is a system folder."))
    return out


def refusal(path: str) -> str:
    """A plain reason ``path`` cannot be used, or "" when it can."""
    real = _canon(path)
    if os.path.dirname(real) == real:
        return "That is a whole drive. Pick a folder on it."
    for place, why in _wrong_places():
        if _inside_or_equal(real, place):
            return why
        if _inside_or_equal(place, real) and "system" not in why:
            return ("That folder has your vault, your sign-in keys or "
                    "BuildBot's settings inside it.")
    return ""


# ---------------------------------------------------------------------------
# changing the data folder
# ---------------------------------------------------------------------------

def _write(values: dict[str, Any]) -> None:
    from puffpai import setup_ops
    setup_ops.write_main_config({"knowledge": values})


def set_data_dir(folder: str) -> dict[str, Any]:
    """Use ``folder`` from now on. ``{ok, data_dir, lines}`` or ``{ok: False,
    message}``. Never moves or deletes anything in the old folder."""
    raw = str(folder or "").strip().strip('"')
    if not raw or "\x00" in raw:
        return {"ok": False, "message": "Type or pick a folder first."}
    path = os.path.abspath(os.path.expanduser(raw))
    why = refusal(path)
    if why:
        return {"ok": False, "message": why + " Pick another folder."}
    try:
        os.makedirs(os.path.join(path, MY_FILES), exist_ok=True)
    except OSError:
        return {"ok": False, "message": "I could not make that folder. "
                                        "Check the drive is there and try again."}
    old = data_dir()
    lines = [f"Downloads and anything you add go to {path}.", free_space_line(path)]
    values: dict[str, Any] = {"data_dir": path, "zim_dir": path}
    if old and _canon(old) != _canon(path):
        before = [p for p in previous_dirs() if _canon(p) != _canon(path)]
        if os.path.isdir(old) and all(_canon(p) != _canon(old) for p in before):
            before.append(old)
        values["previous_dirs"] = before[-10:]
        if os.path.isdir(old):
            lines.append(MOVE_NOTE)
    _write(values)
    _sync_manifest(path)
    poke()
    return {"ok": True, "data_dir": path, "lines": lines}


def _sync_manifest(path: str) -> None:
    """The build manifest's ``storage.knowledge_dir`` mirrors the setting."""
    try:
        from puffpai import build_manifest
        manifest = build_manifest.read_manifest()
        if not isinstance(manifest, dict):
            return
        storage = manifest.get("storage") if isinstance(manifest.get("storage"), dict) else {}
        if storage.get("knowledge_dir") == path:
            return
        storage["knowledge_dir"] = path
        manifest["storage"] = storage
        build_manifest.write_manifest(manifest)
    except Exception as exc:  # noqa: BLE001 - the config is the setting
        logger.debug("manifest knowledge_dir not synced: %s", type(exc).__name__)


# ---------------------------------------------------------------------------
# linked folders
# ---------------------------------------------------------------------------

def _link_id(real: str) -> str:
    return "lf" + hashlib.sha1(_canon(real).encode("utf-8")).hexdigest()[:10]


def link_folder(folder: str, name: str = "") -> dict[str, Any]:
    """Link a folder she already has. ``{ok, folder}`` or ``{ok: False, message}``."""
    raw = str(folder or "").strip().strip('"')
    if not raw or "\x00" in raw:
        return {"ok": False, "message": "Type or pick a folder first."}
    path = os.path.abspath(os.path.expanduser(raw))
    if not os.path.isdir(path):
        return {"ok": False, "message": "I could not find that folder."}
    why = refusal(path)
    if why:
        return {"ok": False, "message": why + " Pick another folder."}
    real = os.path.realpath(path)
    rows = linked_folders()
    for row in rows:
        if _canon(row["path"]) == _canon(real):
            return {"ok": True, "folder": row, "already": True}
    row = {"id": _link_id(real), "path": real,
           "name": (str(name or "").strip()[:80] or os.path.basename(real) or real),
           "added_at": _now()}
    _write({"linked_folders": rows + [row]})
    poke()
    return {"ok": True, "folder": row}


def unlink_folder(folder_id: str) -> dict[str, Any]:
    """Unlink a folder and take its files out of the index. Never touches
    the folder itself."""
    rows = linked_folders()
    keep = [r for r in rows if r.get("id") != folder_id]
    if len(keep) == len(rows):
        return {"ok": False, "message": "No linked folder has that id."}
    _write({"linked_folders": keep})
    removed = _drop_root(folder_id)
    return {"ok": True, "removed_from_index": removed}


# ---------------------------------------------------------------------------
# the scan state
# ---------------------------------------------------------------------------

def _state_path() -> str:
    from puffpai.config import get_state_dir
    return os.path.join(get_state_dir(), "knowledge", "my_data.json")


def _load() -> dict[str, Any]:
    try:
        with open(_state_path(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict) and isinstance(data.get("files"), dict):
            return data
    except (OSError, ValueError):
        pass
    return {"files": {}, "last_scan": ""}


def _save(state: dict[str, Any]) -> None:
    path = _state_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=1, ensure_ascii=False)
    os.replace(tmp, path)


def doc_id_for(path: str, root_id: str) -> str:
    digest = hashlib.sha1(_canon(path).encode("utf-8")).hexdigest()[:16]
    return ("lf_" if root_id.startswith("lf") else "mf_") + digest


def _roots() -> list[dict[str, Any]]:
    """Every folder the watcher reads: ``{id, path, kind, name}``."""
    out = [{"id": "mine", "path": data_dir(), "kind": "data", "name": MY_FILES}]
    for n, p in enumerate(previous_dirs()):
        out.append({"id": f"prev{n}", "path": p, "kind": "previous",
                    "name": os.path.basename(p.rstrip("\\/")) or p})
    for r in linked_folders():
        out.append({"id": r["id"], "path": r["path"], "kind": "linked",
                    "name": r.get("name") or r["path"]})
    seen: set[str] = set()
    unique = []
    for r in out:
        c = _canon(r["path"])
        if c in seen:
            continue
        seen.add(c)
        unique.append(r)
    return unique


def _hidden(entry: os.DirEntry) -> bool:
    if entry.name.startswith((".", "~$")):
        return True
    try:
        st = entry.stat(follow_symlinks=False)
    except OSError:
        return True
    return bool(getattr(st, "st_file_attributes", 0) & _HIDDEN_ATTRS)


def _is_link(entry: os.DirEntry) -> bool:
    try:
        if entry.is_symlink():
            return True
        st = entry.stat(follow_symlinks=False)
    except OSError:
        return True
    return bool(getattr(st, "st_file_attributes", 0) & 0x400)  # reparse point


def _book_folder(name: str) -> bool:
    from puffpai.knowledge import text_books
    return name.endswith((text_books.BOOK_SUFFIX, ".part"))


def _walk(root: str, *, data_root: bool) -> tuple[list[tuple[str, os.stat_result]], bool]:
    """``([(path, stat)], cut_short)``. Never follows a link."""
    found: list[tuple[str, os.stat_result]] = []
    stack = [root]
    while stack:
        folder = stack.pop()
        try:
            entries = list(os.scandir(folder))
        except OSError:
            continue
        for e in entries:
            if _hidden(e) or _is_link(e):
                continue
            try:
                if e.is_dir(follow_symlinks=False):
                    low = e.name.lower()
                    if low in _SKIP_DIRS or (data_root and _book_folder(e.name)):
                        continue
                    stack.append(e.path)
                    continue
                if not e.is_file(follow_symlinks=False):
                    continue
            except OSError:
                continue
            if os.path.splitext(e.name)[1].lower() not in WATCH_EXTS:
                continue
            low = e.name.lower()
            if low.endswith(_OWN_SUFFIXES) or low.startswith(_OWN_PREFIXES):
                continue
            try:
                found.append((e.path, e.stat(follow_symlinks=False)))
            except OSError:
                continue
            if len(found) >= MAX_FILES_PER_FOLDER:
                return found, True
    return found, False


# ---------------------------------------------------------------------------
# the scan
# ---------------------------------------------------------------------------

def _vault(vault):
    if vault is not None:
        return vault
    from puffpai.ingestion import _resolve_shared_vault
    return _resolve_shared_vault()


def _vault_open(vault) -> bool:
    try:
        return bool(vault.is_initialized and getattr(vault, "is_unlocked", False))
    except Exception:  # noqa: BLE001
        return False


def _drop_doc(doc_id: str) -> bool:
    from puffpai import ingestion
    try:
        report = ingestion.delete_ingested_doc_report(doc_id)
    except Exception as exc:  # noqa: BLE001
        logger.info("could not take %s out of the index: %s", doc_id, type(exc).__name__)
        return False
    return bool(report.get("ok")) or report.get("reason") == "No indexed document has that id."


def _drop_root(root_id: str) -> int:
    with _lock:
        state = _load()
        files = state["files"]
        gone = 0
        for key in [k for k, v in files.items() if v.get("root") == root_id]:
            entry = files[key]
            if entry.get("status") in (INDEXED, IN_VAULT) and entry.get("doc_id"):
                _drop_doc(entry["doc_id"])
                gone += 1
            del files[key]
        _save(state)
    return gone


SEALED = "sealed"
#: Plain words when an upload with a key or a personal detail cannot be sealed.
NOT_KEPT_TEXT = ("This file has a key, a password or a personal detail in it, and "
                 "your vault is not ready to take it, so it was not kept. Set up "
                 "or open your vault with your PIN, then add it again.")
#: The scan state is written at most every this many files or seconds.
SAVE_EVERY_FILES = 200
SAVE_EVERY_SECONDS = 5.0


def hold_upload(data: bytes, name: str, *, relative: str = "", hint: Optional[str] = None,
                auto_apply: bool = False, vault=None) -> dict[str, Any]:
    """An upload with a key or a personal detail while the vault is locked:
    seal the whole file into the vault's write-only inbox (no PIN needed) so
    no plain copy stays on disk. It is put back into My files and added the
    first time the scan finds the vault open. ``{status, sealed}``; status
    ``sealed`` or ``refused`` (the inbox could not take it: nothing kept)."""
    from puffpai.security import vault_inbox
    active = _vault(vault)
    try:
        status, box = vault_inbox.seal_file(active.vault_dir, data, {
            "name": name, "relative": relative, "hint": hint,
            "auto_apply": bool(auto_apply)})
    except Exception as exc:  # noqa: BLE001 - not sealed means not kept
        logger.info("could not seal %s: %s", name, type(exc).__name__)
        status, box = "error", ""
    if status != vault_inbox.SEALED:
        logger.info("%s not kept: the vault inbox said %s", name, status)
        return {"status": "refused", "sealed": ""}
    with _lock:
        state = _load()
        state["files"]["sealed:" + box] = {"root": "mine", "doc_id": "", "name": name,
                                           "upload": True, "status": SEALED, "at": _now()}
        _save(state)
    logger.info("%s sealed for the vault", name)
    return {"status": SEALED, "sealed": box}


def ingest_one(path: str, *, root_id: str, doc_id: str, vault=None,
               upload: bool = False, hint: Optional[str] = "notes",
               auto_apply: bool = False) -> dict[str, Any]:
    """Run one file through the upload path. Returns its state entry.

    An upload (her data given to BuildBot) that has to wait for the vault is
    sealed and its plain bytes deleted (:func:`hold_upload`); the entry then
    carries ``_sealed`` (the key it lives under now). A file of hers that
    stays where she keeps it simply waits."""
    from puffpai import ingestion
    name = os.path.basename(path)
    result = ingestion.ingest_file(
        path, original_filename=name, user_hint=hint, auto_apply=auto_apply,
        vault=vault, file_id=doc_id, keep_original=not upload,
        hold_when_locked=True,
        source="upload" if upload else ("linked_folder" if root_id.startswith("lf")
                                         else "data_folder"))
    entry: dict[str, Any] = {"root": root_id, "doc_id": doc_id, "name": name,
                             "upload": bool(upload), "at": _now()}
    if result.waiting_for_vault and upload:
        with open(path, "rb") as fh:
            data = fh.read()
        rel = ""
        try:
            rel = os.path.relpath(path, my_files_dir()).replace("\\", "/")
        except ValueError:
            pass
        held = hold_upload(data, name, relative="" if rel.startswith("..") else rel,
                           hint=hint, auto_apply=auto_apply, vault=vault)
        data = b""
        try:
            os.remove(path)
        except OSError:
            logger.warning("could not remove the plain copy of %s", name)
        if held["status"] == SEALED:
            entry["status"] = SEALED
            entry["_sealed"] = "sealed:" + held["sealed"]
        else:
            entry["status"] = "refused"
            result.error = NOT_KEPT_TEXT
            result.waiting_for_vault = False
    elif result.waiting_for_vault:
        entry["status"] = WAITING
    elif result.error:
        entry["status"] = UNREADABLE
    elif upload and result.vaulted and not os.path.exists(path):
        # The upload's plain copy was removed once its details were in the vault.
        entry["status"] = IN_VAULT
    else:
        entry["status"] = INDEXED
    entry["vaulted"] = bool(result.vaulted)
    entry["_result"] = result
    return entry


def record(path: str, entry: dict[str, Any]) -> None:
    """Remember a file the upload route already ran, so a scan does not redo it.
    A sealed upload is kept under its sealed name instead of its old path."""
    sealed = entry.get("_sealed")
    entry = {k: v for k, v in entry.items() if not k.startswith("_")}
    try:
        st = os.stat(path)
        entry["mtime"], entry["size"] = st.st_mtime, st.st_size
    except OSError:
        entry["mtime"], entry["size"] = 0, 0
    with _lock:
        state = _load()
        if sealed:
            state["files"].pop(_canon(path), None)
        elif entry.get("status") == "refused":
            state["files"].pop(_canon(path), None)
        else:
            state["files"][_canon(path)] = entry
        _save(state)


def _restore_sealed(active) -> int:
    """With the vault open: put every sealed upload back into My files and
    add it (screen, redact, index, vault). Returns how many."""
    from puffpai.security import vault_inbox
    done = 0
    for box in vault_inbox.sealed_files(active.vault_dir):
        got = vault_inbox.open_sealed_file(active, box)
        if got is None:
            continue
        meta, data = got
        path = save_upload(data, str(meta.get("name") or "file"), str(meta.get("relative") or ""))
        data = b""
        doc_id = doc_id_for(path, "mine")
        record(path, {"root": "mine", "doc_id": doc_id, "name": os.path.basename(path),
                      "upload": True, "status": UNREADABLE, "at": _now()})
        entry = ingest_one(path, root_id="mine", doc_id=doc_id, vault=active, upload=True,
                           hint=meta.get("hint"), auto_apply=bool(meta.get("auto_apply")))
        entry.pop("_result", None)
        record(path, entry)
        vault_inbox.remove_sealed_file(active.vault_dir, box)
        with _lock:
            state = _load()
            state["files"].pop("sealed:" + box, None)
            _save(state)
        done += 1
    if done:
        logger.info("put back and added %d upload(s) that waited for the vault", done)
    return done


class _Batch:
    """Scan results kept in memory and written in batches: merged onto what
    is on disk, so an upload recorded meanwhile is never lost."""

    def __init__(self) -> None:
        self.put: dict[str, dict[str, Any]] = {}
        self.gone: set[str] = set()
        self.pending = 0
        self.last = time.monotonic()

    def set(self, key: str, entry: dict[str, Any]) -> None:
        self.gone.discard(key)
        self.put[key] = entry
        self.pending += 1

    def drop(self, key: str) -> None:
        self.put.pop(key, None)
        self.gone.add(key)
        self.pending += 1

    def maybe_flush(self) -> None:
        if (self.pending >= SAVE_EVERY_FILES
                or time.monotonic() - self.last >= SAVE_EVERY_SECONDS):
            self.flush()

    def flush(self, **extra: Any) -> None:
        if not self.pending and not extra:
            return
        with _lock:
            state = _load()
            for key in self.gone:
                state["files"].pop(key, None)
            state["files"].update(self.put)
            state.update(extra)
            _save(state)
        self.put, self.gone, self.pending = {}, set(), 0
        self.last = time.monotonic()


def scan_once(*, vault=None, ingest: Optional[Callable[..., dict]] = None) -> dict[str, Any]:
    """One pass over the data folder, earlier data folders and linked folders.

    Returns ``{added, changed, removed, waiting, too_big, unreadable,
    cut_short, restored}``. The state file is written in batches
    (:data:`SAVE_EVERY_FILES` files or :data:`SAVE_EVERY_SECONDS`), and once
    at the end.
    """
    counts = {"added": 0, "changed": 0, "removed": 0, "waiting": 0,
              "too_big": 0, "unreadable": 0, "cut_short": 0, "restored": 0}
    if not bool(getattr(_cfg().knowledge, "auto_index", True)):
        return {**counts, "off": True}
    ingest = ingest or ingest_one
    cap = max_file_bytes()
    active = None
    with _lock:
        has_sealed = any(k.startswith("sealed:") for k in _load()["files"])
    if has_sealed:
        active = _vault(vault)
        if _vault_open(active):
            counts["restored"] = _restore_sealed(active)
    batch = _Batch()
    with _lock:
        roots = _roots()
        files: dict[str, dict[str, Any]] = _load()["files"]
    seen: dict[str, tuple[str, str, os.stat_result]] = {}
    present_roots = set()
    for root in roots:
        if not os.path.isdir(root["path"]):
            continue  # a drive that is unplugged: keep what it had
        present_roots.add(root["id"])
        got, cut = _walk(root["path"], data_root=root["kind"] != "linked")
        counts["cut_short"] += int(cut)
        for path, st in got:
            seen.setdefault(_canon(path), (path, root["id"], st))
    live_ids = {r["id"] for r in roots}
    for key, entry in list(files.items()):
        if key in seen or key.startswith("sealed:"):
            continue
        root_id = str(entry.get("root") or "")
        if root_id in live_ids and root_id not in present_roots:
            continue
        if entry.get("status") == IN_VAULT:
            continue  # an upload whose plain copy went once it was vaulted
        if entry.get("doc_id") and entry.get("status") == INDEXED:
            _drop_doc(entry["doc_id"])
        batch.drop(key)
        counts["removed"] += 1
    todo = []
    for key, (path, root_id, st) in seen.items():
        entry = files.get(key)
        same = (entry is not None and entry.get("mtime") == st.st_mtime
                and entry.get("size") == st.st_size)
        if same and entry.get("status") != WAITING:
            continue
        if same and entry.get("status") == WAITING:
            if active is None:
                active = _vault(vault)
            if not _vault_open(active):
                counts["waiting"] += 1
                continue
        if st.st_size > cap:
            batch.set(key, {"root": root_id, "doc_id": "", "name": os.path.basename(path),
                            "status": TOO_BIG, "mtime": st.st_mtime,
                            "size": st.st_size, "at": _now()})
            counts["too_big"] += 1
            continue
        todo.append((key, path, root_id, st, entry))
    _watcher["indexing"] = len(todo)
    for key, path, root_id, st, entry in todo:
        doc_id = (entry or {}).get("doc_id") or doc_id_for(path, root_id)
        try:
            new = ingest(path, root_id=root_id, doc_id=doc_id, vault=vault,
                         upload=bool((entry or {}).get("upload")))
        except Exception as exc:  # noqa: BLE001 - one bad file never stops the rest
            logger.info("could not read %s: %s", os.path.basename(path), type(exc).__name__)
            new = {"root": root_id, "doc_id": doc_id, "name": os.path.basename(path),
                   "status": UNREADABLE, "at": _now()}
        sealed = new.get("_sealed")
        new = {k: v for k, v in new.items() if not k.startswith("_")}
        new["mtime"], new["size"] = st.st_mtime, st.st_size
        if sealed or new["status"] == "refused":
            batch.drop(key)  # the plain copy is gone; a sealed one is recorded already
        else:
            batch.set(key, new)
        _watcher["indexing"] = max(0, _watcher["indexing"] - 1)
        batch.maybe_flush()
        if new["status"] in (WAITING, SEALED):
            counts["waiting"] += 1
        elif new["status"] == UNREADABLE:
            counts["unreadable"] += 1
        elif entry is None:
            counts["added"] += 1
        else:
            counts["changed"] += 1
    batch.flush(last_scan=_now())
    _watcher["indexing"] = 0
    if any(counts[k] for k in ("added", "changed", "removed", "waiting", "too_big", "restored")):
        logger.info("data folders: %d added, %d changed, %d removed, %d waiting "
                    "for the vault, %d too big, %d put back", counts["added"],
                    counts["changed"], counts["removed"], counts["waiting"],
                    counts["too_big"], counts["restored"])
    return counts


def remove_upload_file(doc_id: str) -> bool:
    """Delete an upload's file from My files (the Documents page's delete).
    Only a file BuildBot was given (an upload) and only inside
    ``<data_dir>/My files``; never a linked folder or anywhere else."""
    base = _canon(my_files_dir())
    removed = False
    with _lock:
        state = _load()
        for key, entry in list(state["files"].items()):
            if entry.get("doc_id") != doc_id or not entry.get("upload"):
                continue
            if key.startswith("sealed:") or str(entry.get("root", "")).startswith("lf"):
                continue
            if not _inside_or_equal(key, base) or key == base:
                continue
            try:
                if os.path.isfile(key):
                    os.remove(key)
                    removed = True
            except OSError as exc:
                logger.info("could not remove %s: %s", entry.get("name"), type(exc).__name__)
                continue
            del state["files"][key]
        _save(state)
    return removed


# ---------------------------------------------------------------------------
# the thread
# ---------------------------------------------------------------------------

def poke() -> None:
    """Scan soon (after an upload, a new link, a new folder, an unlock)."""
    _wake.set()


def start_watcher(*, interval: float = SCAN_SECONDS) -> Optional[threading.Thread]:
    """Start the polling thread once per process (it scans at once)."""
    with _lock:
        thread = _watcher.get("thread")
        if thread is not None and thread.is_alive():
            return thread

        def loop() -> None:
            while True:
                try:
                    scan_once()
                except Exception:  # noqa: BLE001 - never let the watcher die
                    logger.exception("data folder scan failed")
                _wake.wait(interval)
                _wake.clear()

        thread = threading.Thread(target=loop, name="puffpai-my-data", daemon=True)
        _watcher["thread"] = thread
        thread.start()
        return thread


# ---------------------------------------------------------------------------
# what the Knowledge page and /installed show
# ---------------------------------------------------------------------------

def status() -> dict[str, Any]:
    state = _load()
    files = state["files"]
    folders = []
    waiting_total = 0
    for root in _roots():
        mine = [v for v in files.values() if v.get("root") == root["id"]]
        by = {s: sum(1 for v in mine if v.get("status") == s)
              for s in (INDEXED, WAITING, TOO_BIG, UNREADABLE, IN_VAULT, SEALED)}
        by[WAITING] += by[SEALED]
        waiting_total += by[WAITING]
        folders.append({
            "id": root["id"], "kind": root["kind"], "name": root["name"],
            "path": root["path"], "exists": os.path.isdir(root["path"]),
            "files": len(mine), "indexed": by[INDEXED] + by[IN_VAULT],
            "waiting": by[WAITING], "too_big": by[TOO_BIG],
            "unreadable": by[UNREADABLE],
            "waiting_names": [v.get("name") for v in mine
                              if v.get("status") == WAITING][:20],
        })
    k = _cfg().knowledge
    folder = data_dir()
    return {
        "data_dir": folder,
        "my_files": os.path.join(folder, MY_FILES),
        "free_space": free_space_line(folder),
        "auto_index": bool(getattr(k, "auto_index", True)),
        "max_file_mb": max_file_bytes() // (1024 * 1024),
        "folders": folders,
        "linked": linked_folders(),
        "last_scan": state.get("last_scan") or "",
        "indexing": int(_watcher.get("indexing") or 0),
        "waiting": waiting_total,
        "waiting_line": WAITING_LINE.format(n=waiting_total) if waiting_total else "",
        "scan_seconds": SCAN_SECONDS,
        "recent_files": recent_my_files(),
    }


#: How many of the newest files in My files the Knowledge page lists (each
#: with its Open button, POST /files/open).
RECENT_MAX = 30


def recent_my_files(limit: int = RECENT_MAX) -> list[dict[str, Any]]:
    """The newest files in My files: ``[{"name", "folder", "path", "modified"}]``
    (``folder`` is the subfolder inside My files, "" at its top). Hidden
    files are skipped; at most :data:`MAX_FILES_PER_FOLDER` are looked at."""
    root = os.path.join(data_dir(), MY_FILES)
    found: list[tuple[float, str, str]] = []
    seen = 0
    for here, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for n in names:
            seen += 1
            if seen > MAX_FILES_PER_FOLDER:
                break
            if n.startswith((".", "~$")):
                continue
            p = os.path.join(here, n)
            try:
                found.append((os.path.getmtime(p), p, os.path.relpath(here, root)))
            except OSError:
                continue
    found.sort(reverse=True)
    return [{"name": os.path.basename(p), "folder": "" if rel == "." else rel, "path": p,
             "modified": datetime.fromtimestamp(t, timezone.utc).isoformat(timespec="seconds")}
            for t, p, rel in found[:limit]]


def installed_rows() -> list[dict[str, Any]]:
    """One row for My files and one per linked folder (``installed.py``).
    Folder names only: a full path never goes into the brain's prompt."""
    rows = []
    files = _load()["files"].values()
    if any(v.get("status") in (INDEXED, IN_VAULT) and not str(v.get("root", "")).startswith("lf")
           for v in files):
        # Only once something of hers is in it: an empty folder is not news.
        rows.append({"id": "my-files", "name": MY_FILES,
                     "what": "Your own files in your data folder, read and kept up to date"})
    for r in linked_folders():
        rows.append({"id": r["id"], "name": str(r.get("name") or "Linked folder"),
                     "what": "A folder of yours, linked and read where it is"})
    return rows


# ---------------------------------------------------------------------------
# saving uploads into My files
# ---------------------------------------------------------------------------

_BAD = re.compile(r'[<>:"|?*\x00-\x1f]')


def upload_target(filename: str, relative: str = "") -> str:
    """Where an upload lands: ``My files/<its folder path>/<name>``, never
    outside My files and never over an existing file."""
    base = my_files_dir(create=True)
    parts = [p for p in re.split(r"[\\/]+", str(relative or "")) if p]
    parts = parts[:-1] if parts and parts[-1] == os.path.basename(str(filename or "")) else parts
    clean = []
    for p in parts[-6:]:
        p = _BAD.sub("_", p).strip(" .")
        if p and p not in (".", "..") and not p.startswith("."):
            clean.append(p[:80])
    name = _BAD.sub("_", os.path.basename(str(filename or "file").replace("\\", "/"))).strip(" .")
    if not name or name.startswith("."):
        name = "file" + (name or "")
    folder = os.path.join(base, *clean)
    real = _canon(folder)
    if not _inside_or_equal(real, _canon(base)):
        folder = base
    os.makedirs(folder, exist_ok=True)
    stem, ext = os.path.splitext(name[:120])
    target = os.path.join(folder, stem + ext)
    n = 2
    while os.path.exists(target):
        target = os.path.join(folder, f"{stem} ({n}){ext}")
        n += 1
    return target


def save_upload(data: bytes, filename: str, relative: str = "") -> str:
    target = upload_target(filename, relative)
    with open(target, "xb") as fh:
        fh.write(data)
    return target


def add_upload(data: bytes, filename: str, *, relative: str = "", hint: Optional[str] = None,
               auto_apply: bool = True, vault=None) -> dict[str, Any]:
    """Save an upload into My files and run it at once. Returns the
    ingestion result as a dict plus ``saved_to`` and ``status``."""
    path = save_upload(data, filename, relative)
    doc_id = doc_id_for(path, "mine")
    # Known before it is read, so a scan running now does not take it for a
    # file dropped by hand (which would keep its plain copy).
    record(path, {"root": "mine", "doc_id": doc_id, "name": os.path.basename(path),
                  "upload": True, "status": UNREADABLE, "at": _now()})
    entry = ingest_one(path, root_id="mine", doc_id=doc_id, vault=vault, upload=True,
                       hint=hint, auto_apply=auto_apply)
    result = entry.pop("_result")
    record(path, entry)
    out = result.to_dict()
    out["saved_to"] = os.path.relpath(path, my_files_dir()) if os.path.exists(path) else ""
    # Sealed for the vault reads as waiting: it is added by itself on unlock.
    out["status"] = WAITING if entry["status"] == SEALED else entry["status"]
    return out


__all__ = [
    "MY_FILES", "SCAN_SECONDS", "WATCH_EXTS", "data_dir", "my_files_dir",
    "previous_dirs", "linked_folders", "set_data_dir", "link_folder",
    "unlink_folder", "refusal", "scan_once", "start_watcher", "poke", "status",
    "installed_rows", "add_upload", "save_upload", "upload_target", "record",
    "doc_id_for", "ingest_one", "free_space_line", "hold_upload",
    "remove_upload_file",
]
