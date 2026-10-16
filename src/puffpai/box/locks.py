"""Recorded dependency locks for code built or installed in the locked box.

Nothing updates by itself:

* **npm.** With the project's own ``package-lock.json``: ``npm ci
  --ignore-scripts``. If that fails, or there is no lock: ``npm install
  --ignore-scripts`` once. The lock that results is scanned (:func:`scan_npm_lock`:
  every package comes from ``https://registry.npmjs.org/`` with an integrity
  hash; ``git+``, ``file:``, ``http:`` and other hosts are held as
  ``lock_offsite``) and recorded with its sha256 under
  ``<state>/integrations/<id>/lock/``. Every later build or reinstall is
  ``npm ci`` from the recorded lock, and a project whose own lock differs
  from the recorded one is refused (``lock_changed``).
* **pip.** First install: ``pip install --only-binary=:all: --report``; the
  report becomes a requirements lock with one sha256 per wheel
  (:func:`pip_lock_from_report`). Later: ``pip install --require-hashes
  --no-deps --only-binary=:all: -r <lock>``.
* No release-binary downloads.

The argv lists here are run by :func:`puffpai.box.driver.run` with
``what="box_install"`` and only the package site's hosts; install phases
never get an owner folder.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any, Optional

NPM_REGISTRY = "https://registry.npmjs.org/"
PYPI_FILES = "https://files.pythonhosted.org/"
NPM_HOSTS = ["registry.npmjs.org"]
PIP_HOSTS = ["pypi.org", "files.pythonhosted.org"]
NPM_LOCK = "package-lock.json"
PIP_LOCK = "requirements.lock"
MAX_LOCK_BYTES = 20_000_000

NPM_CI = ["npm", "ci", "--ignore-scripts", "--no-audit", "--no-fund"]
NPM_INSTALL = ["npm", "install", "--ignore-scripts", "--no-audit", "--no-fund"]
VENV = ["python3", "-m", "venv", "/work/venv"]
PIP = "/work/venv/bin/pip"

_SHA512_RE = re.compile(r"^sha512-[A-Za-z0-9+/]{80,}={0,2}\Z")
_HEX64_RE = re.compile(r"^[0-9a-f]{64}\Z")
_PIP_NAME_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9._-]{0,98}[A-Za-z0-9])?\Z")
_PIP_VERSION_RE = re.compile(r"^[0-9][0-9A-Za-z.+!_-]{0,40}\Z")
_NPM_NAME_RE = re.compile(r"^(@[a-z0-9-~][a-z0-9-._~]{0,100}/)?[a-z0-9-~][a-z0-9-._~]{0,100}\Z")
_NPM_VERSION_RE = re.compile(r"^\d{1,6}\.\d{1,6}\.\d{1,6}([-+][0-9A-Za-z.-]{1,60})?\Z")


class LockError(ValueError):
    """A lock that may not be used. ``code`` is a Drawbridge reason code."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(detail or code)
        self.code = code
        self.detail = detail


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ── npm ──────────────────────────────────────────────────────────────────────

def _npm_entries(lock: dict) -> list[tuple[str, dict]]:
    """Every package entry of a v1, v2 or v3 lockfile, ``(name, entry)``."""
    out: list[tuple[str, dict]] = []
    packages = lock.get("packages")
    if isinstance(packages, dict):
        for key, entry in packages.items():
            if key == "" or not isinstance(entry, dict):
                continue                                  # the project itself
            parts = str(key).replace("\\", "/").split("/")
            if "node_modules" not in parts and ".." not in parts and ":" not in str(key) \
                    and not str(key).startswith("/"):
                continue          # one of the project's own folders (a workspace): its source
            out.append((str(key), entry))
        return out

    def walk(deps: Any, prefix: str) -> None:
        if not isinstance(deps, dict):
            return
        for name, entry in deps.items():
            if isinstance(entry, dict):
                out.append((prefix + str(name), entry))
                walk(entry.get("dependencies"), prefix + str(name) + "/")
    walk(lock.get("dependencies"), "")
    return out


def scan_npm_lock(data: bytes) -> list[str]:
    """Problems in a ``package-lock.json`` (``[]`` when every package comes
    from the npm registry over https with an integrity hash).

    A workspace link inside the project (``"link": true`` with a relative
    ``resolved``) is not a download and is allowed. Anything else without a
    registry ``resolved`` is a problem."""
    if len(data) > MAX_LOCK_BYTES:
        return ["the list of parts is too big"]
    try:
        lock = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return ["the list of parts cannot be read"]
    if not isinstance(lock, dict):
        return ["the list of parts cannot be read"]
    problems: list[str] = []
    for name, entry in _npm_entries(lock):
        resolved = entry.get("resolved")
        if entry.get("link") is True:
            if isinstance(resolved, str) and ":" not in resolved and not resolved.startswith(
                    ("/", "\\", "..")) and ".." not in resolved.split("/"):
                continue
            problems.append(f"{name}: a link outside the project")
            continue
        if entry.get("bundled") is True and not resolved:
            continue                                     # shipped inside its parent
        version = str(entry.get("version") or "")
        if not isinstance(resolved, str) or not resolved:
            if version.startswith(("git+", "git:", "github:", "file:", "http:", "https:", "link:")):
                problems.append(f"{name}: comes from {version.split(':', 1)[0]}")
            else:
                problems.append(f"{name}: no download address")
            continue
        # The prefix ends in "/", so the host is exactly the registry (no
        # "registry.npmjs.org.evil" and no user@host trick can match it).
        if not resolved.startswith(NPM_REGISTRY) or any(c in resolved for c in " \\\t\r\n"):
            problems.append(f"{name}: comes from somewhere other than the npm registry")
            continue
        if not isinstance(entry.get("integrity"), str) or not entry["integrity"].strip():
            problems.append(f"{name}: no fingerprint")
    return problems


# ── pip ──────────────────────────────────────────────────────────────────────

def pip_lock_parts(data: bytes) -> tuple[str, list[str]]:
    """``(requirements lock text, [off-site parts])`` from ``pip install
    --report``: one ``name==version --hash=sha256:...`` line per wheel.

    Raises :class:`LockError` (``no_hashes``) for a report that cannot be
    read or a part without a proper name, version or sha256. A part that is
    not a wheel from files.pythonhosted.org is listed as off-site (a hold)."""
    try:
        report = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise LockError("no_hashes", "the install report cannot be read") from None
    installs = report.get("install") if isinstance(report, dict) else None
    if not isinstance(installs, list) or not installs:
        raise LockError("no_hashes", "the install report lists nothing")
    lines = []
    offsite = []
    for item in installs:
        if not isinstance(item, dict):
            raise LockError("no_hashes", "an odd install entry")
        meta = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        name = str(meta.get("name") or "")
        version = str(meta.get("version") or "")
        info = item.get("download_info") if isinstance(item.get("download_info"), dict) else {}
        url = str(info.get("url") or "")
        hashes = ((info.get("archive_info") or {}).get("hashes") or {}) \
            if isinstance(info.get("archive_info"), dict) else {}
        digest = str(hashes.get("sha256") or "").lower()
        if not _PIP_NAME_RE.match(name) or not _PIP_VERSION_RE.match(version):
            raise LockError("no_hashes", "a part without a proper name or version")
        if not _HEX64_RE.match(digest):
            raise LockError("no_hashes", f"{name} has no fingerprint")
        if not url.startswith(PYPI_FILES) or not url.split("#", 1)[0].endswith(".whl"):
            offsite.append(f"{name}: not a wheel from PyPI")
        lines.append(f"{name}=={version} --hash=sha256:{digest}")
    return "\n".join(sorted(lines, key=str.lower)) + "\n", offsite


def pip_lock_from_report(data: bytes) -> str:
    """A ``--require-hashes`` requirements lock from ``pip install --report``.

    Raises :class:`LockError` (``lock_offsite``) for anything that is not a
    wheel from files.pythonhosted.org with a sha256, and (``no_hashes``) when
    a hash is missing."""
    text, offsite = pip_lock_parts(data)
    if offsite:
        raise LockError("lock_offsite", offsite[0])
    return text


def pip_lock_count(lock_text: str) -> int:
    return sum(1 for line in lock_text.splitlines() if "--hash=sha256:" in line)


# ── the argv of each step ────────────────────────────────────────────────────

def npm_package_install(name: str, version: str) -> list[str]:
    """First install of one exact npm package into ``/work``."""
    if not _NPM_NAME_RE.match(name or "") or not _NPM_VERSION_RE.match(version or ""):
        raise LockError("not_exact", "not an exact package name and version")
    return NPM_INSTALL + ["--save-exact", f"{name}@{version}"]


def pip_first_install(name: str, version: str) -> list[str]:
    if not _PIP_NAME_RE.match(name or "") or not _PIP_VERSION_RE.match(version or ""):
        raise LockError("not_exact", "not an exact package name and version")
    return [PIP, "install", "--only-binary=:all:", "--no-cache-dir", "--no-input",
            "--report", "/work/.pip-report.json", f"{name}=={version}"]


def pip_rebuild() -> list[str]:
    return [PIP, "install", "--require-hashes", "--no-deps", "--only-binary=:all:",
            "--no-cache-dir", "--no-input", "-r", f"/work/{PIP_LOCK}"]


def npm_steps(*, project_has_lock: bool, recorded: Optional[dict]) -> list[list[str]]:
    """The npm install steps, in order; the caller stops at the first that
    works (the first build) or runs exactly the one (a rebuild)."""
    if recorded is not None:
        return [NPM_CI]
    if project_has_lock:
        return [NPM_CI, NPM_INSTALL]
    return [NPM_INSTALL]


# ── the record ───────────────────────────────────────────────────────────────

def lock_dir(integration_id: str) -> str:
    from puffpai.integrate import records
    return os.path.join(records.folder(integration_id), "lock")


def record(integration_id: str, kind: str, data: bytes) -> dict:
    """Write the lock and return ``{"kind","path","sha256"}`` for the record."""
    if kind not in ("npm", "pip"):
        raise LockError("no_hashes", "unknown lock kind")
    folder = lock_dir(integration_id)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, NPM_LOCK if kind == "npm" else PIP_LOCK)
    tmp = path + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, path)
    digest = sha256(data)
    with open(os.path.join(folder, "lock.json"), "w", encoding="utf-8") as fh:
        json.dump({"kind": kind, "sha256": digest, "file": os.path.basename(path)}, fh)
    return {"kind": kind, "path": path, "sha256": digest}


def recorded(integration_id: str) -> Optional[dict]:
    """The recorded lock: ``{"kind","path","sha256","data"}``, or None. A lock
    file whose bytes no longer match the recorded sha256 raises
    :class:`LockError` (``lock_changed``)."""
    folder = lock_dir(integration_id)
    try:
        with open(os.path.join(folder, "lock.json"), "r", encoding="utf-8") as fh:
            meta = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(meta, dict) or meta.get("kind") not in ("npm", "pip"):
        raise LockError("lock_changed", "the record of the lock is damaged")
    name = NPM_LOCK if meta["kind"] == "npm" else PIP_LOCK
    path = os.path.join(folder, name)
    try:
        with open(path, "rb") as fh:
            data = fh.read(MAX_LOCK_BYTES + 1)
    except OSError:
        raise LockError("lock_changed", "the recorded lock is missing") from None
    if sha256(data) != meta.get("sha256"):
        raise LockError("lock_changed", "the recorded lock was changed")
    return {"kind": meta["kind"], "path": path, "sha256": meta["sha256"], "data": data}


def check_same(recorded_lock: dict, project_lock: Optional[bytes]) -> None:
    """A rebuild uses the recorded lock; a project that brings a DIFFERENT
    lock of its own is refused (``lock_changed``)."""
    if project_lock is None:
        return
    if sha256(project_lock) != recorded_lock["sha256"]:
        raise LockError("lock_changed", "the project's own list of parts changed")


__all__ = ["LockError", "NPM_CI", "NPM_HOSTS", "NPM_INSTALL", "NPM_LOCK", "PIP_HOSTS",
           "PIP_LOCK", "check_same", "npm_package_install", "npm_steps",
           "pip_first_install", "pip_lock_count", "pip_lock_from_report", "pip_lock_parts", "pip_rebuild",
           "record", "recorded", "scan_npm_lock", "sha256"]
