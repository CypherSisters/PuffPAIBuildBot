"""The mood board: colours and themes read from the owner's own pictures.

BuildBot's own code (``provenance.by = "buildbot"``), goal-agnostic: it knows
colours, not weddings. Plan kind ``folder_palette`` adds a "Mood board" tile
that asks for a folder. After she picks one from a paired browser
(``POST /integrations/<id>/folder`` with a ``folder_give`` ticket, and
``owner_folders.check(path, "ro")``), :func:`build`:

* reads at most 500 raster pictures (at most 20 MB each) from that folder,
  NEVER writing to it, never following a link out of it;
* re-encodes each through :func:`pictures.reencode` (SVG and other kinds are
  skipped), quantizes it to 6 colours (median cut);
* clusters every picture's colours into 3 to 6 themes (k-means in Lab space,
  numpy, a fixed seed, so the same pictures always give the same themes);
* names each theme from a fixed colour-word table ("soft pink and gold");
* saves ``palette.json`` and re-encoded thumbnails (at most 400 px) under
  ``<state>/pictures/<shelf>/``.

One ``reads`` action, "Show the colours and themes from your pictures",
answers from ``palette.json``.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import hashlib
import os
import threading
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from puffpai import fsutil
from puffpai.integrate import pictures, records, wording
from puffpai.logging_config import get_logger

logger = get_logger(__name__)

KIND = "folder_palette"
CHECKER_VERSION = "r3.1"
MAX_FILES = 500
MAX_BYTES = pictures.MAX_OWN_BYTES
THUMB_SIDE = 400
MAX_THUMBS = 60
SEED = 20260927
COLOURS_PER_PICTURE = 6
BLOCK_CODES = frozenset({"not_raster", "too_big", "bad_picture", "folder"})
HOLD_CODES = frozenset()
ACTION = "show_colours"
_RASTER = (".jpg", ".jpeg", ".png", ".webp", ".gif")

#: A fixed table of everyday colour words in Lab-ish terms: (word, L, a, b).
COLOUR_WORDS = (
    ("white", 96, 0, 2), ("ivory", 93, -1, 12), ("cream", 90, 2, 18),
    ("light grey", 78, 0, 0), ("grey", 55, 0, 0), ("charcoal", 30, 0, 0),
    ("black", 10, 0, 0), ("soft pink", 82, 18, 5), ("pink", 68, 38, 5),
    ("rose", 55, 45, 12), ("red", 45, 62, 42), ("burgundy", 28, 38, 12),
    ("coral", 66, 42, 32), ("peach", 80, 18, 28), ("orange", 64, 38, 64),
    ("rust", 45, 35, 42), ("gold", 74, 6, 58), ("mustard", 66, 4, 62),
    ("yellow", 88, -6, 78), ("sage", 70, -14, 14), ("olive", 50, -12, 42),
    ("green", 55, -44, 36), ("forest green", 34, -28, 18), ("mint", 86, -24, 8),
    ("teal", 50, -28, -8), ("sky blue", 76, -10, -24), ("blue", 45, 10, -52),
    ("navy", 22, 12, -36), ("lavender", 74, 14, -20), ("purple", 38, 42, -42),
    ("plum", 32, 32, -14), ("brown", 40, 14, 24), ("tan", 70, 6, 24),
    ("beige", 82, 2, 14),
)


class PaletteError(ValueError):
    def __init__(self, line: str, code: str = "folder"):
        super().__init__(line)
        self.line = line
        self.code = code


# -- colour maths (numpy, no model) -------------------------------------------------

def _srgb_to_lab(rgb):
    import numpy as np
    c = np.asarray(rgb, dtype=np.float64) / 255.0
    c = np.where(c > 0.04045, ((c + 0.055) / 1.055) ** 2.4, c / 12.92)
    m = np.array([[0.4124, 0.3576, 0.1805], [0.2126, 0.7152, 0.0722],
                  [0.0193, 0.1192, 0.9505]])
    xyz = c @ m.T / np.array([0.95047, 1.0, 1.08883])
    f = np.where(xyz > 216 / 24389, np.cbrt(xyz), (24389 / 27 * xyz + 16) / 116)
    lab = np.stack([116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]),
                    200 * (f[..., 1] - f[..., 2])], axis=-1)
    return lab


def _kmeans(points, weights, k: int, *, seed: int = SEED, rounds: int = 30):
    """Weighted k-means with a fixed seed (k-means++ start)."""
    import numpy as np
    rng = np.random.default_rng(seed)
    pts = np.asarray(points, dtype=np.float64)
    w = np.asarray(weights, dtype=np.float64)
    first = int(np.argmax(w))
    centres = [pts[first]]
    for _ in range(1, k):
        d = np.min(((pts[:, None, :] - np.array(centres)[None]) ** 2).sum(-1), axis=1) * w
        if d.sum() <= 0:
            break
        centres.append(pts[int(rng.choice(len(pts), p=d / d.sum()))])
    centres = np.array(centres)
    for _ in range(rounds):
        label = np.argmin(((pts[:, None, :] - centres[None]) ** 2).sum(-1), axis=1)
        new = np.array([np.average(pts[label == i], axis=0, weights=w[label == i])
                        if (label == i).any() else centres[i] for i in range(len(centres))])
        if np.allclose(new, centres):
            break
        centres = new
    label = np.argmin(((pts[:, None, :] - centres[None]) ** 2).sum(-1), axis=1)
    return centres, label


def colour_word(lab) -> str:
    L, a, b = (float(x) for x in lab)
    best = min(COLOUR_WORDS, key=lambda row: (row[1] - L) ** 2 * 0.6 + (row[2] - a) ** 2
               + (row[3] - b) ** 2)
    return best[0]


def _hex(rgb) -> str:
    return "#" + "".join(f"{int(max(0, min(255, round(v)))):02x}" for v in rgb)


def picture_colours(clean: bytes) -> list[tuple[tuple[int, int, int], int]]:
    """The 6 main colours of one (already re-encoded) picture, with weights."""
    import io
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = pictures.MAX_PIXELS
    with Image.open(io.BytesIO(clean)) as img:
        small = img.convert("RGB")
        small.thumbnail((160, 160))
        quant = small.quantize(colors=COLOURS_PER_PICTURE, method=Image.Quantize.MEDIANCUT)
        pal = quant.getpalette() or []
        counts = quant.getcolors() or []
    out = []
    for count, index in sorted(counts, reverse=True)[:COLOURS_PER_PICTURE]:
        rgb = tuple(pal[index * 3:index * 3 + 3])
        if len(rgb) == 3:
            out.append((rgb, int(count)))
    return out


def themes_from(colours: list[tuple[tuple[int, int, int], int]]) -> list[dict]:
    """3 to 6 themes from every picture's colours (fewer only when there are
    fewer distinct colours)."""
    import numpy as np
    if not colours:
        return []
    rgb = np.array([c for c, _w in colours], dtype=np.float64)
    weights = np.array([w for _c, w in colours], dtype=np.float64)
    lab = _srgb_to_lab(rgb)
    distinct = len({tuple(int(v) for v in c) for c, _w in colours})
    k = max(1, min(6, distinct, max(3, int(round(len(colours) ** 0.5)))))
    centres, label = _kmeans(lab, weights, k)
    themes = []
    for i in range(len(centres)):
        members = label == i
        if not members.any():
            continue
        member_lab = lab[members]
        member_rgb = rgb[members]
        member_w = weights[members]
        order = np.argsort(-member_w)
        words: list[str] = []
        swatches: list[str] = []
        for j in order:
            word = colour_word(member_lab[j])
            if word not in words:
                words.append(word)
            sw = _hex(member_rgb[j])
            if sw not in swatches:
                swatches.append(sw)
        name = " and ".join(words[:2]) if len(words) > 1 else words[0]
        themes.append({"name": name, "colours": swatches[:5],
                       "weight": round(float(member_w.sum() / weights.sum()), 4)})
    themes.sort(key=lambda t: -t["weight"])
    seen: set[str] = set()
    out = []
    for t in themes:
        if t["name"] in seen:
            continue
        seen.add(t["name"])
        out.append(t)
    return out[:6]


# -- reading her folder (never writing to it) ------------------------------------------

def _files(root: str) -> list[str]:
    """Raster files under *root*, sorted, no links followed, at most 500."""
    from puffpai.integrate import classify
    out = []
    for rel, _full, size in classify._walk(root):  # noqa: SLF001 - skips links/reparse points
        if os.path.splitext(rel)[1].lower() in _RASTER and size <= MAX_BYTES:
            out.append(rel)
    return sorted(out)[:MAX_FILES]


def build(realpath: str, shelf: str, *, progress: Callable[[str], None] = lambda _l: None
          ) -> dict[str, Any]:
    """Read the folder at *realpath* and write the palette onto *shelf*.
    Returns the saved palette. The folder itself is only ever read."""
    folder = pictures.shelf_dir(shelf)
    thumbs_dir = os.path.join(folder, "thumbs")
    os.makedirs(thumbs_dir, exist_ok=True)
    colours: list[tuple[tuple[int, int, int], int]] = []
    read = skipped = 0
    thumbs: list[str] = []
    for rel in _files(realpath):
        full = os.path.join(realpath, *rel.split("/"))
        try:
            with open(full, "rb") as fh:
                data = fh.read(MAX_BYTES + 1)
            clean, ext = pictures.reencode(data, max_bytes=MAX_BYTES, max_side=THUMB_SIDE)
        except (OSError, pictures.PictureRefused):
            skipped += 1
            continue
        read += 1
        colours.extend(picture_colours(clean))
        if len(thumbs) < MAX_THUMBS:
            name = hashlib.sha256(rel.encode("utf-8")).hexdigest()[:16] + ext
            fsutil.atomic_write(os.path.join(thumbs_dir, name), clean)
            thumbs.append(name)
    themes = themes_from(colours)
    palette = {"schema": "palette/v1", "pictures_read": read, "skipped": skipped,
               "themes": themes, "thumbs": thumbs,
               "made_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    fsutil.atomic_write_json(os.path.join(folder, "palette.json"), palette)
    progress(wording.PALETTE_MADE.format(n=read, themes=len(themes)))
    return palette


def read_palette(shelf: str) -> Optional[dict]:
    try:
        data = fsutil.read_json(os.path.join(pictures.shelf_dir(shelf), "palette.json"))
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


# -- the applier, the folder choice, the action ------------------------------------------

_APPLY_LOCK = threading.Lock()
#: The run's shared state remembers the board this run made (its id only).
_BOARD_THIS_RUN = "palette_board_this_run"


def _existing_board() -> Optional[dict]:
    """The Mood board this bot already has (BuildBot's own, ready), or None."""
    for rec in records.all():
        if rec.get("mode") == KIND and rec.get("status") == "ready" \
                and (rec.get("from") or {}).get("source") == "buildbot":
            return rec
    return None


def apply_palette(item: dict, ctx: Any) -> dict[str, Any]:
    """Plan applier ``folder_palette``: the Mood board tile, waiting for a folder.

    One Mood board per bot: a plan has two lines that both need it ("use your
    own pictures" and "colours and themes"), and a later run or the Add page
    may ask again. Every call after the first gives back the board already
    made, with its own one-time line, never a second board (acceptance: the
    wedding build made two and said so four times).

    The line is returned once and not also sent as progress: the factory
    shows the progress lines and then the result line, so saying both
    doubled every line."""
    name = wording.PALETTE_NAME
    shared = getattr(ctx, "shared", None)
    with _APPLY_LOCK:
        rec = _existing_board()
        if rec is not None:
            # The same run's second plan line gives back the very line the
            # first one said, so the closing screen shows ONE Mood board line; a later run
            # (the Add page) says it uses the board already there.
            made_now = isinstance(shared, dict) and shared.get(_BOARD_THIS_RUN) == rec["id"]
            return {"status": "added",
                    "line": wording.PALETTE_ADDED if made_now else wording.PALETTE_SAME,
                    "lines": [], "integration_id": rec["id"], "undo": dict(rec["undo"])}
        rec = records.blank(records.new_id(name), name=name, purpose=wording.PALETTE_PURPOSE,
                            by="wizard", need=str(item.get("need") or "") or None)
        rec["mode"] = KIND
        rec["status"] = "ready"
        rec["pictures_dir"] = pictures.shelf_name(rec["id"])
        rec["from"]["source"] = "buildbot"
        rec["added_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        records.save(rec)
        if isinstance(shared, dict):
            shared[_BOARD_THIS_RUN] = rec["id"]
    return {"status": "added", "line": wording.PALETTE_ADDED, "lines": [],
            "integration_id": rec["id"], "undo": dict(rec["undo"])}


def palette_rows(draft: dict) -> list[dict[str, str]]:
    """Drawbridge rows for her folder: it passed the folder check, and every
    picture that was kept opened as an ordinary picture."""
    ok_folder = bool(draft.get("folder_ok"))
    rows = [{"check": "A folder you may give", "result": "pass" if ok_folder else "fail",
             "why": "The folder is one of yours." if ok_folder else str(
                 draft.get("folder_why") or "That folder cannot be given."),
             "code": "folder"}]
    return rows


def choose_folder(integration_id: str, path: str, *, device_id: str) -> dict[str, Any]:
    """Her folder for the mood board, from a paired device. Mints and redeems
    the ``folder_give`` ticket for exactly this app, folder and read-only
    mode, then reads the folder. Raises :class:`PaletteError`."""
    from puffpai import drawbridge
    from puffpai.integrate import optional
    from puffpai.security import consent_tickets
    if not consent_tickets.usable_device(device_id):
        raise PaletteError(wording.DECIDE_NOT_PAIRED, "not_paired")
    rec = records.get(integration_id)
    if rec is None or rec.get("mode") != KIND or rec.get("status") != "ready":
        raise PaletteError(wording.UNKNOWN_ITEM, "unknown")
    owner_folders = optional("puffpai.security.owner_folders")
    check = getattr(owner_folders, "check", None) if owner_folders else None
    target_fn = getattr(consent_tickets, "target_folder", None)
    if check is None or target_fn is None:
        raise PaletteError(wording.NOT_IN_BUILD, "not_in_build")
    real, why = check(path, "ro")
    verdict = drawbridge.check_addition({"kind": KIND, "name": rec["name"],
                                         "folder_ok": bool(real), "folder_why": why},
                                        by="toolsmith")
    if not real or verdict.get("verdict") != drawbridge.ALLOW:
        raise PaletteError(why or wording.FOLDER_REFUSED, "folder")
    app_label = rec["id"]
    try:
        target = target_fn(app_label, real, "ro")
        ticket = consent_tickets.mint("folder_give", target,
                                      wording.PALETTE_FOLDER_YES.format(name=rec["name"]),
                                      device_id)
    except consent_tickets.TicketRefused as exc:
        raise PaletteError(exc.message, exc.code) from None
    except ValueError:
        raise PaletteError(wording.FOLDER_REFUSED, "folder") from None
    ok, code = consent_tickets.redeem(ticket, device_id, "folder_give", target)
    if not ok:
        raise PaletteError(consent_tickets.refusal_words(code), code)
    # Checked again right before reading: a folder swapped for a link to
    # somewhere else since the first check is refused, not read.
    again, why = check(path, "ro")
    if not again or os.path.normcase(again) != os.path.normcase(real):
        raise PaletteError(why or wording.FOLDER_REFUSED, "folder")
    palette = build(real, rec["pictures_dir"])
    rec["folder"] = {"granted": True, "mode": "ro",
                     "realpath_sha": hashlib.sha256(real.encode("utf-8")).hexdigest()}
    rec["actions"] = [f"{rec['id']}__{ACTION}"]
    records.save(rec)
    return {"ok": True, "line": wording.PALETTE_MADE.format(
        n=palette["pictures_read"], themes=len(palette["themes"])), "palette": palette}


def list_actions() -> list[dict]:
    out = []
    for rec in records.all():
        if rec.get("mode") != KIND or rec.get("status") != "ready" \
                or not (rec.get("folder") or {}).get("granted"):
            continue
        out.append({
            "id": f"{rec['id']}__{ACTION}", "integration": rec["id"],
            "app_name": rec["name"], "says": wording.PALETTE_SAYS, "effect": "reads",
            "kind": "palette",
            "args": {"type": "object", "additionalProperties": False, "properties": {},
                     "required": []},
            "undoable": False,
            "provenance": {"by": "buildbot", "compiler_version": CHECKER_VERSION,
                           "app_sha": "", "recipe_sha": ""},
            "checked": {"verdict": "allow", "effect_proven": "reads", "rows_sha": "",
                        "at": rec.get("added_at") or ""}})
    return out


def run_action(action: dict, args: dict, ctx: dict) -> dict:
    rec = records.get(str(action.get("integration") or ""))
    palette = read_palette(rec["pictures_dir"]) if rec else None
    if not palette:
        return {"ok": True, "data": {"rows": []}, "line": ""}
    rows = [{"name": t["name"], "colours": ", ".join(t.get("colours") or [])}
            for t in palette.get("themes") or []]
    return {"ok": True, "data": {"rows": rows}, "line": ""}


__all__ = ["ACTION", "BLOCK_CODES", "CHECKER_VERSION", "COLOUR_WORDS", "HOLD_CODES", "KIND",
           "PaletteError", "apply_palette", "build", "choose_folder", "colour_word",
           "list_actions", "palette_rows", "picture_colours", "read_palette", "run_action",
           "themes_from"]
