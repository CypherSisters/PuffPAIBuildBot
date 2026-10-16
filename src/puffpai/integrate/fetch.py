"""Download a ticked project at exactly the version pinned when the plan was made.

The archive address is built here from the ``found/v1`` record only
(``repo.host``, ``repo.owner``, ``repo.name``, ``repo.pinned_commit``), never
from anything a request carried:

* GitHub: ``https://codeload.github.com/<o>/<r>/tar.gz/<pinned_commit>``
* Codeberg: ``https://codeberg.org/<o>/<r>/archive/<pinned_commit>.tar.gz``

No pinned commit, no download (``not_pinned``). The download goes through the
one exit for code (``source_fetch.get_archive(service="code_download")``, at
most 200 MB) into ``<state>/integrations/<id>/download.tar.gz``, and is
unpacked with NTFS-safe rules (``safe_archive.extract``) into
``extraction_root()/<id>/src/``, which is outside the state, keys and vault
folders. A re-fetch passes the recorded sha-256; a different archive is refused
(``pin_changed``). Nothing downloaded is ever run or opened here.

When the download or unpack helpers are not in this build, nothing is fetched
(:class:`FetchRefused` with code ``not_in_build``).

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from typing import Any, Optional

from puffpai.integrate import optional, records
from puffpai.logging_config import get_logger

logger = get_logger(__name__)

#: Big real apps are normal: the bounds are only
#: for disk space, far above any app people run themselves.
MAX_ARCHIVE_BYTES = 4 * 1024 * 1024 * 1024
MAX_EXTRACTED_BYTES = 10 * 1024 * 1024 * 1024
MAX_FILES = 200_000
#: Above this an app's card says plainly how much disk space it takes.
BIG_NOTE_BYTES = 1024 * 1024 * 1024
SERVICE = "code_download"

#: Forge -> (archive host, archive address template).
FORGES = {
    "github.com": ("codeload.github.com",
                   "https://codeload.github.com/{owner}/{name}/tar.gz/{commit}"),
    "codeberg.org": ("codeberg.org",
                     "https://codeberg.org/{owner}/{name}/archive/{commit}.tar.gz"),
}
_PART_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,99}\Z")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}\Z")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}\Z")


class FetchRefused(Exception):
    """Nothing was fetched. ``code`` is a Drawbridge-style reason code."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


def _repo(found: Any) -> dict[str, Any]:
    repo = found.get("repo") if isinstance(found, dict) else None
    return repo if isinstance(repo, dict) else {}


def archive_url(found: dict) -> tuple[str, str]:
    """``(url, archive host)`` for *found*'s pinned commit. Raises FetchRefused."""
    repo = _repo(found)
    host = str(repo.get("host") or "").lower()
    if host not in FORGES:
        raise FetchRefused("wrong_host", host[:60])
    owner, name = str(repo.get("owner") or ""), str(repo.get("name") or "")
    if not (_PART_RE.fullmatch(owner) and _PART_RE.fullmatch(name)) or \
            owner in (".", "..") or name in (".", ".."):
        raise FetchRefused("wrong_host", "owner or name is not plain")
    commit = str(repo.get("pinned_commit") or "").lower()
    if not _COMMIT_RE.fullmatch(commit):
        raise FetchRefused("not_pinned")
    archive_host, template = FORGES[host]
    return template.format(owner=owner, name=name, commit=commit), archive_host


def _inside(path: str, parent: str) -> bool:
    try:
        path, parent = os.path.realpath(path), os.path.realpath(parent)
        return os.path.commonpath([path, parent]) == parent and path != parent
    except ValueError:  # different drives
        return False


def archive_commit(path: str) -> str:
    """The commit a forge archive says it holds, or "".

    ``git archive`` (behind codeload.github.com and Codeberg's archives)
    writes the commit as the archive's first header, a global pax record
    ``52 comment=<40 hex>``. Only that first header is read (at most 1.5 KB
    unpacked), so a hostile archive cannot make this hold much."""
    import gzip
    import zlib
    try:
        with gzip.open(path, "rb") as fh:
            head = fh.read(512)
            if len(head) < 512 or head[156:157] != b"g":
                return ""
            size = int(head[124:136].rstrip(b"\0 ").decode("ascii") or "0", 8)
            if not 0 < size <= 1024:
                return ""
            body = fh.read(size)
    except (OSError, EOFError, ValueError, UnicodeDecodeError, zlib.error):
        return ""
    for record in body.split(b"\n"):
        _length, _, pair = record.partition(b" ")
        key, _, value = pair.partition(b"=")
        if key == b"comment":
            text = value.decode("ascii", "replace").strip().lower()
            return text if _COMMIT_RE.fullmatch(text) else ""
    return ""


def download_path(integration_id: str) -> str:
    return os.path.join(records.folder(integration_id), "download.tar.gz")


def src_dir(integration_id: str) -> str:
    """``extraction_root()/<id>/src``; raises FetchRefused without the helper."""
    safe_archive = optional("puffpai.security.safe_archive")
    if safe_archive is None:
        raise FetchRefused("not_in_build", "safe_archive")
    if not records.valid_id(integration_id):
        raise FetchRefused("bad_id")
    return os.path.join(safe_archive.extraction_root(), integration_id, "src")


def fetch(integration_id: str, found: dict, *,
          expect_sha256: Optional[str] = None) -> dict[str, Any]:
    """Download and unpack *found* at its pinned commit.

    Returns ``{"url", "commit", "path", "sha256", "bytes", "final_host",
    "src_dir", "files", "extracted_bytes", "skipped", "fetched_at"}``.
    Raises :class:`FetchRefused` (``wrong_host``, ``not_pinned``,
    ``pin_changed``, ``too_big``, ``not_in_build``, ``download_failed``,
    ``extract_failed``).
    """
    url, archive_host = archive_url(found)
    commit = str(_repo(found).get("pinned_commit") or "").lower()
    if expect_sha256 is not None and not _SHA256_RE.match(str(expect_sha256)):
        raise FetchRefused("pin_changed", "recorded fingerprint is not a sha-256")
    source_fetch = optional("puffpai.source_fetch")
    get_archive = getattr(source_fetch, "get_archive", None) if source_fetch else None
    safe_archive = optional("puffpai.security.safe_archive")
    if get_archive is None or safe_archive is None:
        raise FetchRefused("not_in_build", "archive download")
    allowed = {archive_host}
    keyed: dict[str, Any] = {}
    if _repo(found).get("private") is True:
        # A private project of hers (puffpai.github_account): only inside the
        # build's Yes for its own setup row, through GitHub's signed-in archive
        # address. The token rides only that first request, inside the
        # security package; the address names the commit, never the token.
        try:
            from puffpai import github_account
            url, allowed, key_id = github_account.private_archive(found)
        except Exception as exc:  # noqa: BLE001 - not allowed now: nothing fetched
            raise FetchRefused("download_failed",
                               str(getattr(exc, "code", "") or "private")) from None
        keyed = {"key_id": key_id}
    dest = download_path(integration_id)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    try:
        got = get_archive(url, allowed_hosts=set(allowed),
                          max_bytes=MAX_ARCHIVE_BYTES, dest_path=dest,
                          service=SERVICE, expect_sha256=expect_sha256,
                          max_redirects=2, **keyed)
    except Exception as exc:  # noqa: BLE001 - every failure is "not fetched"
        code = str(getattr(exc, "code", "") or "")
        logger.info("integrate: download refused (%s)", code or type(exc).__name__)
        if code == "sha_mismatch":
            raise FetchRefused("pin_changed") from None
        if code == "too_big":
            raise FetchRefused("too_big") from None
        if code in ("host", "redirects", "inside_address"):
            raise FetchRefused("wrong_host", code) from None
        raise FetchRefused("download_failed", code) from None
    sha = str((got or {}).get("sha256") or "").lower()
    final_host = str((got or {}).get("final_host") or "").lower()
    size = int((got or {}).get("bytes") or 0)
    if not _SHA256_RE.match(sha):
        raise FetchRefused("download_failed", "no fingerprint")
    if expect_sha256 is not None and sha != expect_sha256.lower():
        # get_archive checks this too; checked again because a stored pin is
        # the whole promise of "exactly the version the plan showed".
        raise FetchRefused("pin_changed")
    if final_host != archive_host:
        raise FetchRefused("wrong_host", final_host[:60])
    if size > MAX_ARCHIVE_BYTES:
        raise FetchRefused("too_big")
    # The address named the commit; the archive itself must say it holds that
    # commit, or "exactly the version the plan showed" is only a promise
    # about an address. (No archive fingerprint exists before the first
    # download: the plan pins the commit.)
    if archive_commit(str((got or {}).get("path") or dest)) != commit:
        raise FetchRefused("not_pinned", "the archive holds another commit")
    root = safe_archive.extraction_root()
    target = os.path.join(root, integration_id, "src")
    state = os.path.realpath(records._state_dir())  # noqa: SLF001 - same package
    if not _inside(target, root) or _inside(target, state) or _inside(state, root):
        raise FetchRefused("extract_failed", "unpack folder is not in the apps folder")
    if os.path.lexists(target):
        rm = getattr(safe_archive, "safe_rmtree", None)
        if rm is None:
            raise FetchRefused("not_in_build", "safe_rmtree")
        rm(target, allowed_roots=[os.path.join(root, integration_id)])
    os.makedirs(target, exist_ok=True)
    try:
        unpacked = safe_archive.extract(str((got or {}).get("path") or dest), target,
                                        max_total_bytes=MAX_EXTRACTED_BYTES,
                                        max_files=MAX_FILES, strip_top=True)
    except Exception as exc:  # noqa: BLE001
        code = str(getattr(exc, "code", "") or "")
        logger.info("integrate: unpack refused (%s)", code or type(exc).__name__)
        raise FetchRefused("too_big" if code == "too_big" else "extract_failed",
                           code) from None
    return {"url": url, "commit": commit, "path": str((got or {}).get("path") or dest),
            "sha256": sha, "bytes": size, "final_host": final_host,
            "src_dir": target, "files": int((unpacked or {}).get("files") or 0),
            "extracted_bytes": int((unpacked or {}).get("bytes") or 0),
            "skipped": list((unpacked or {}).get("skipped") or [])[:50],
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}


# ---------------------------------------------------------------------------
# the licence file
# ---------------------------------------------------------------------------

#: family -> (plain words, open for anyone, personal use only)
FAMILIES: dict[str, tuple[str, bool, bool]] = {
    "MIT": ("free to use (MIT)", True, False),
    "Apache-2.0": ("free to use (Apache 2.0)", True, False),
    "BSD-3-Clause": ("free to use (BSD)", True, False),
    "BSD-2-Clause": ("free to use (BSD)", True, False),
    "0BSD": ("free to use (BSD)", True, False),
    "ISC": ("free to use (ISC)", True, False),
    "Unlicense": ("free to use (public domain)", True, False),
    "CC0-1.0": ("free to use (public domain)", True, False),
    "WTFPL": ("free to use (WTFPL)", True, False),
    "Zlib": ("free to use (zlib)", True, False),
    "MPL-2.0": ("free to use (Mozilla)", True, False),
    "LGPL": ("free to use (LGPL)", True, False),
    "GPL-2.0": ("free to use, changes must stay open (GPL 2)", True, False),
    "GPL-3.0": ("free to use, changes must stay open (GPL 3)", True, False),
    "AGPL-3.0": ("free to use, changes must stay open (AGPL)", True, False),
    "CC-BY-4.0": ("free to use with credit (CC BY)", True, False),
    "CC-BY-SA-4.0": ("free to use with credit (CC BY-SA)", True, False),
    "CC-BY-NC": ("for personal use only (CC BY-NC)", False, True),
    "Commons-Clause": ("not free to use (Commons Clause)", False, False),
    "BUSL": ("not free to use (Business Source)", False, False),
    "custom": ("its own licence words", False, False),
    "none": ("no licence", False, False),
}

def family_of(name: Any) -> str:
    """The licence family for an SPDX id or a family name (``custom`` when
    it is not one of the known families, ``none`` when empty)."""
    text = str(name or "").strip()
    if not text or text.lower() in ("none", "noassertion", "unknown", "null", "other"):
        return "none"
    low = text.lower()
    if "commons clause" in low or "commons-clause" in low:
        return "Commons-Clause"
    if low.startswith("busl") or "business source" in low:
        return "BUSL"
    if "-nc" in low or "noncommercial" in low or "non-commercial" in low:
        return "CC-BY-NC"
    for fam in FAMILIES:
        if low == fam.lower():
            return fam
    for prefix, fam in (("agpl-3.0", "AGPL-3.0"), ("lgpl", "LGPL"), ("gpl-3.0", "GPL-3.0"),
                        ("gpl-2.0", "GPL-2.0"), ("cc-by-sa", "CC-BY-SA-4.0"),
                        ("cc-by-4.0", "CC-BY-4.0"), ("cc0", "CC0-1.0"),
                        ("mpl-2.0", "MPL-2.0"), ("apache-2", "Apache-2.0"),
                        ("bsd-3", "BSD-3-Clause"), ("bsd-2", "BSD-2-Clause"),
                        ("mit", "MIT"), ("isc", "ISC"), ("unlicense", "Unlicense")):
        if low.startswith(prefix):
            return fam
    return "custom"


_PROJECT_URL_RE = re.compile(r"^https://(?:www\.)?(?:github\.com|codeberg\.org)/"
                             r"([A-Za-z0-9_.-]{1,100})/([A-Za-z0-9_.-]{1,100})(?:[/?#]|\Z)")


def identity_of(rec: dict) -> tuple[str, str]:
    """``(owner, name)`` of the project from the record's address (never from
    its files), as the plan read its licence; empty when not a code host's."""
    src = rec.get("from") if isinstance(rec, dict) and isinstance(rec.get("from"), dict) else {}
    m = _PROJECT_URL_RE.match(str(src.get("url") or ""))
    return (m.group(1), m.group(2).removesuffix(".git")) if m else ("", "")


def read_license(tree: str, *, identity: Any = ()) -> dict[str, Any]:
    """Read the licence at the top of the unpacked *tree*.

    ``{"file": name | None, "family", "words", "open", "nc"}``.

    One licence reader. A separate list of patterns, checked in order with
    "non-?commercial" first, would read the GPL-2.0, GPL-3.0 and Unlicense
    texts as personal use only and refuse finds the plan offered as free
    after the download. So it reads with the plan's own reader (``catalogs.searchers.github.read_licence_tree``): every licence
    and notice file at the top, by the licences' own sentences, links never
    followed, and anything it does not know is custom (not free). *identity*
    is ``(owner, name)`` from the find's address, as the plan read it.
    """
    try:
        if isinstance(identity, str):
            owner, name = identity, ""
        else:
            owner, name = (list(identity or ()) + ["", ""])[:2]
        from puffpai.catalogs.searchers import github
        got = github.read_licence_tree(tree, str(owner or ""), str(name or ""))
    except Exception:  # noqa: BLE001 - a reader that cannot run confirms nothing
        got = {"file": None, "read": "custom"}
    found_name, read = got.get("file"), got.get("read")
    if found_name is None and read is None:
        fam = "none"
    elif read is None:
        fam = "custom"
    else:
        fam = family_of(read)
        if fam not in FAMILIES or fam == "none":
            fam = "custom"
    words, is_open, nc = FAMILIES[fam]
    return {"file": found_name, "family": fam, "words": words, "open": is_open,
            "nc": nc}


__all__ = ["FAMILIES", "identity_of", "FetchRefused", "MAX_ARCHIVE_BYTES", "MAX_EXTRACTED_BYTES",
           "archive_url", "family_of", "fetch", "read_license", "src_dir"]
