"""Pictures: stock pictures and picture sets, kept as clean copies on a shelf.

Every picture BuildBot keeps is DECODED and RE-ENCODED here
(``picture_set``): raster only (JPEG, PNG, WebP, the first frame of a GIF);
SVG and anything else is refused; ``Image.MAX_IMAGE_PIXELS`` is 40 million
(a decompression bomb is refused before it is decoded); the copy is written
fresh from the pixels, so metadata (EXIF, GPS, comments, ICC tricks) and any
payload hidden after the image data are left behind. At most 5 MB each for
stock pictures, 20 MB each from the owner's own folder.

Stock pictures (plan kind ``picture_set``) come from the find's own host only
(``found.picture.full_host``, fixed when the plan was made) through
``source_fetch.fetch_small(service="picture_download")``, and keep a credit
and licence line each in ``credits.json``. A picture-set PROJECT (a code
download whose files are mostly pictures) is re-encoded from the unpacked
tree the same way.

Shelves live at ``<state>/pictures/<shelf>/``; a shelf name is a plain slug.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import io
import json
import os
import re
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from puffpai import fsutil
from puffpai.integrate import optional, records, wording
from puffpai.logging_config import get_logger

logger = get_logger(__name__)

KIND = "picture_set"
CHECKER_VERSION = "r3.1"
MAX_PIXELS = 40_000_000
MAX_STOCK_BYTES = 5 * 1024 * 1024
MAX_OWN_BYTES = 20 * 1024 * 1024
MAX_SET_PICTURES = 200
FORMATS = ("JPEG", "PNG", "WEBP", "GIF")
BLOCK_CODES = frozenset({"not_raster", "too_big", "bad_picture", "wrong_host", "no_license"})
HOLD_CODES = frozenset()
_SHELF_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}\Z")


class PictureRefused(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _pil():
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    return Image


def sniff(data: bytes) -> str:
    """What the bytes say they are: ``JPEG``, ``PNG``, ``WEBP``, ``GIF``,
    ``SVG`` or ``""``. Read from the bytes, never from a file name."""
    head = bytes(data[:512])
    if head.startswith(b"\xff\xd8\xff"):
        return "JPEG"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "PNG"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "WEBP"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "GIF"
    low = head.lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    if low.startswith(b"<") and (b"<svg" in head.lower() or low.startswith(b"<?xml")):
        return "SVG"
    return ""


def reencode(data: bytes, *, max_bytes: int, max_side: Optional[int] = None) -> tuple[bytes, str]:
    """``(clean bytes, extension)`` for one raster picture, or PictureRefused.

    The picture is decoded (the first frame only), checked against the pixel
    cap, optionally shrunk to *max_side*, and written fresh: JPEG for
    pictures with no transparency, PNG for pictures with it. Nothing of the
    original file but its pixels is kept."""
    if not isinstance(data, (bytes, bytearray)) or not data:
        raise PictureRefused("bad_picture")
    if len(data) > max_bytes:
        raise PictureRefused("too_big")
    kind = sniff(data)
    if kind not in FORMATS:
        raise PictureRefused("not_raster")
    Image = _pil()
    try:
        with Image.open(io.BytesIO(bytes(data))) as img:
            if img.format not in FORMATS or img.format != kind:
                raise PictureRefused("not_raster")
            w, h = img.size
            if w <= 0 or h <= 0 or w * h > MAX_PIXELS:
                raise PictureRefused("too_big")
            img.seek(0)
            frame = img.convert("RGBA")
    except PictureRefused:
        raise
    except Image.DecompressionBombError:
        raise PictureRefused("too_big") from None
    except Exception:  # noqa: BLE001 - anything that does not decode is refused
        raise PictureRefused("bad_picture") from None
    if max_side and max(frame.size) > max_side:
        frame.thumbnail((max_side, max_side))
    alpha = frame.getchannel("A")
    out = io.BytesIO()
    # A new image made from the raw pixels only: no info, no EXIF, no ICC,
    # nothing that followed the image data in the original file.
    if alpha.getextrema()[0] < 255:
        clean = Image.frombytes("RGBA", frame.size, frame.tobytes())
        clean.save(out, format="PNG", optimize=True)
        return out.getvalue(), ".png"
    rgb = frame.convert("RGB")
    clean = Image.frombytes("RGB", rgb.size, rgb.tobytes())
    clean.save(out, format="JPEG", quality=88, optimize=True)
    return out.getvalue(), ".jpg"


# -- shelves ---------------------------------------------------------------------------

def pictures_root() -> str:
    from puffpai.config import get_state_dir
    return os.path.join(get_state_dir(), "pictures")


def shelf_name(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-")[:40].strip("-")
    return slug or "pictures"


def shelf_dir(shelf: str) -> str:
    if not _SHELF_RE.match(str(shelf or "")):
        raise ValueError("not a shelf name")
    return os.path.join(pictures_root(), shelf)


def _credits_path(shelf: str) -> str:
    return os.path.join(shelf_dir(shelf), "credits.json")


def credits(shelf: str) -> list[dict]:
    data = fsutil.read_json(_credits_path(shelf), default=[])
    return data if isinstance(data, list) else []


def keep(shelf: str, data: bytes, *, name: str, credit: dict, max_bytes: int) -> str:
    """Re-encode *data* onto *shelf* as a new file; record its credit.
    Returns the file name."""
    clean, ext = reencode(data, max_bytes=max_bytes)
    folder = shelf_dir(shelf)
    os.makedirs(folder, exist_ok=True)
    base = re.sub(r"[^a-z0-9]+", "-", str(name or "picture").lower()).strip("-")[:40] or "picture"
    n = 1
    while True:
        fname = f"{base}{'' if n == 1 else '-' + str(n)}{ext}"
        if not os.path.exists(os.path.join(folder, fname)):
            break
        n += 1
    fsutil.atomic_write(os.path.join(folder, fname), clean)
    rows = credits(shelf)
    rows.append({"file": fname, **{k: str(v)[:200] for k, v in (credit or {}).items()
                                   if k in ("title", "credit", "license", "from")}})
    fsutil.atomic_write_json(_credits_path(shelf), rows[-2000:])
    return fname


# -- Drawbridge: the picture_set checklist ------------------------------------------

def _row(check: str, ok: Optional[bool], why: str, code: str) -> dict[str, str]:
    return {"check": check, "result": "pass" if ok else ("unknown" if ok is None else "fail"),
            "why": why, "code": code}


def picture_rows(draft: dict) -> list[dict[str, str]]:
    """Rows for one stock picture or one picture-set tree."""
    rows: list[dict[str, str]] = []
    pics = draft.get("pictures") if isinstance(draft.get("pictures"), list) else []
    bad = [p for p in pics if p.get("refused")]
    kinds = {p.get("refused") for p in bad}
    rows.append(_row("Pictures only", "not_raster" not in kinds,
                     "Only ordinary pictures." if "not_raster" not in kinds
                     else "Some of it is not an ordinary picture.", "not_raster"))
    rows.append(_row("Not too big", "too_big" not in kinds,
                     "Each picture is a normal size." if "too_big" not in kinds
                     else "A picture is too big.", "too_big"))
    rows.append(_row("Opens as a picture", "bad_picture" not in kinds,
                     "Each one opened." if "bad_picture" not in kinds
                     else "A picture did not open.", "bad_picture"))
    if "host_ok" in draft:
        rows.append(_row("Comes from the place the plan named", bool(draft.get("host_ok")),
                         "From the site the plan named." if draft.get("host_ok")
                         else "It did not come from the site the plan named.", "wrong_host"))
    if "license" in draft:
        lic = str(draft.get("license") or "")
        rows.append(_row("Free to use", bool(lic),
                         f"Its licence: {lic}." if lic else "Nobody says you may use it.",
                         "no_license"))
    return rows


# -- appliers ---------------------------------------------------------------------------

def _progress(ctx: Any) -> Callable[[str], None]:
    fn = getattr(ctx, "progress", None)
    return fn if callable(fn) else (lambda _l: None)


def _job_name(ctx: Any, need: Optional[str]) -> str:
    shared = getattr(ctx, "shared", None)
    needs = (shared or {}).get("needs") if isinstance(shared, dict) else None
    for job in (needs or {}).get("jobs") or [] if isinstance(needs, dict) else []:
        if isinstance(job, dict) and job.get("id") == need and job.get("name"):
            return str(job["name"])[:60]
    return ""


def _shelf_record(shelf: str, name: str, *, need: Optional[str], by: str) -> dict:
    for rec in records.all():
        if rec.get("mode") == "picture_set" and rec.get("pictures_dir") == shelf \
                and rec.get("status") == "ready":
            return rec
    rec = records.blank(records.new_id(name), name=name,
                        purpose=wording.PICTURES_PURPOSE, by=by, need=need)
    rec["mode"] = "picture_set"
    rec["pictures_dir"] = shelf
    rec["from"]["source"] = "pictures"
    return rec


def apply_picture(item: dict, ctx: Any) -> dict[str, Any]:
    """Plan applier ``picture_set``: one stock picture onto its job's shelf."""
    from puffpai import drawbridge, source_fetch
    say = _progress(ctx)
    label = str(item.get("label") or "A picture")[:120]
    find_parts = optional("puffpai.wizard.find_parts")
    found_for = getattr(find_parts, "found_for", None) if find_parts else None
    fetch_small = getattr(source_fetch, "fetch_small", None)
    if found_for is None or fetch_small is None:
        return {"status": "held", "line": f"{label}: {wording.NOT_IN_BUILD}", "lines": []}
    found = found_for(str(item.get("id") or ""))
    pic = found.get("picture") if isinstance(found, dict) else None
    if not isinstance(pic, dict):
        return {"status": "failed", "line": f"{label}: {wording.PICTURE_GONE}", "lines": []}
    url = str(pic.get("full_url") or found.get("url") or "")
    host = str(pic.get("full_host") or "").lower()
    m = re.match(r"^https://([a-z0-9.-]+)(?::443)?/", url)
    host_ok = bool(m and host and m.group(1) == host)
    online = getattr(ctx, "online", None)
    if online is False:
        return {"status": "failed", "line": f"{label}: {wording.OFFLINE}", "lines": []}
    need = str((found.get("meaning") or {}).get("job") or "") or None
    job = _job_name(ctx, need)
    shelf = shelf_name(job or "pictures")
    title = str(found.get("friendly") or found.get("name") or "picture")[:80]
    credit = {"title": title, "credit": str(pic.get("credit") or "")[:200],
              "license": str(pic.get("license") or "")[:60], "from": host}
    pics: list[dict] = []
    data = b""
    if host_ok:
        try:
            got = fetch_small(url, allowed_hosts={host}, max_bytes=MAX_STOCK_BYTES,
                              service="picture_download")
        except Exception as exc:  # noqa: BLE001 - one picture never sinks the build
            logger.info("pictures: download failed: %s", type(exc).__name__)
            got = {}
        got = got if isinstance(got, dict) else {}
        if got.get("reason") == "too_big" or got.get("truncated"):
            pics.append({"refused": "too_big"})
        elif got.get("reason") != "ok" or got.get("status") != 200 \
                or not isinstance(got.get("body"), (bytes, bytearray)):
            return {"status": "failed", "line": f"{label}: {wording.DOWNLOAD_FAILED}",
                    "lines": []}
        else:
            data = bytes(got["body"])
    if not pics:
        try:
            reencode(data, max_bytes=MAX_STOCK_BYTES)
            pics.append({"ok": True})
        except PictureRefused as exc:
            pics.append({"refused": exc.code})
    verdict = drawbridge.check_addition({"kind": KIND, "name": title, "pictures": pics,
                                         "host_ok": host_ok, "license": credit["license"]},
                                        by="toolsmith")
    if verdict.get("verdict") != drawbridge.ALLOW:
        first = next(iter(verdict.get("reasons") or []), "")
        line = wording.STOPPED.format(reason=wording.block_words(first))
        say(line)
        return {"status": "refused", "line": f"{title}: {line}", "lines": [line]}
    rec = _shelf_record(shelf, (f"Pictures: {job}" if job else "Pictures")[:80],
                        need=need, by="wizard")
    keep(shelf, data, name=title, credit=credit, max_bytes=MAX_STOCK_BYTES)
    rec["status"] = "ready"
    rec["added_at"] = rec.get("added_at") or datetime.now(timezone.utc).isoformat(
        timespec="seconds")
    records.save(rec)
    line = wording.PICTURE_ADDED.format(title=title, shelf=rec["name"])
    say(line)
    return {"status": "added", "line": line, "lines": [line], "integration_id": rec["id"],
            "undo": dict(rec["undo"])}


def import_tree(rec: dict, src_root: str) -> dict[str, Any]:
    """A picture-set project: every raster picture in the unpacked tree,
    re-encoded onto a shelf named after it. Returns
    ``{"kept", "refused": [codes], "shelf"}``."""
    from puffpai.integrate import classify
    shelf = shelf_name(rec.get("name") or rec["id"])
    kept = 0
    refused: list[str] = []
    for rel, full, size in classify._walk(src_root):  # noqa: SLF001 - no links followed
        ext = os.path.splitext(rel)[1].lower()
        if ext == ".svg":
            refused.append("not_raster")
            continue
        if ext not in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
            continue
        if kept >= MAX_SET_PICTURES:
            break
        if size > MAX_STOCK_BYTES:
            refused.append("too_big")
            continue
        try:
            with open(full, "rb") as fh:
                data = fh.read(MAX_STOCK_BYTES + 1)
            keep(shelf, data, name=os.path.splitext(os.path.basename(rel))[0],
                 credit={"title": os.path.basename(rel)[:80],
                         "credit": (rec.get("from") or {}).get("url", ""),
                         "license": (rec.get("from") or {}).get("license_read", ""),
                         "from": "project"},
                 max_bytes=MAX_STOCK_BYTES)
            kept += 1
        except PictureRefused as exc:
            refused.append(exc.code)
    return {"kept": kept, "refused": refused, "shelf": shelf}


__all__ = ["BLOCK_CODES", "CHECKER_VERSION", "FORMATS", "HOLD_CODES", "KIND", "MAX_OWN_BYTES",
           "MAX_PIXELS", "MAX_STOCK_BYTES", "PictureRefused", "apply_picture", "credits",
           "import_tree", "keep", "picture_rows", "pictures_root", "reencode", "shelf_dir",
           "shelf_name", "sniff"]
