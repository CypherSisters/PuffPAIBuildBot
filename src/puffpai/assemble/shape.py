"""What one part's data looks like: one real sample, and numbered fields.

Deterministic, no model. :func:`fetch_sample` takes ONE small sample through
the Bridge (``egress_doors.egress_get``) under the part's own switch; a switch
that is off is recorded as ``switch_off`` with the switch's id, so a fix card
can offer to turn it on, and nothing is fetched. :func:`candidates` reads the
sample and lists what an app could show, each with a number the brain picks
by: the item arrays (simple JSONPath such as ``$.dataseries[*]``) and in each
item its title, picture, date, map position, stream links (``.m3u8``,
YouTube, audio) and searchable text.

RSS/Atom feeds and ``#EXTM3U`` stream lists are first turned into the same
plain shape (``{"items": [...]}``), so one reader serves all of them.

The helpers :func:`items_at`, :func:`value_at`, :func:`fill_template` and
:func:`rows_from` are the ones an assembled app's server uses to turn a
source's answer into rows, so the proof and the app read data the same way.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable, Optional

SAMPLE_MAX_BYTES = 512 * 1024
FETCH_TIMEOUT = 20.0
#: A slow free service gets this many tries, waiting this long between them.
FETCH_TRIES = 3
FETCH_BACKOFF_S = (2.0, 5.0)
#: Answers worth asking again (busy or briefly down), never refusals.
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
MAX_DEPTH = 6
MAX_ARRAYS = 8
MAX_FIELDS = 40
EXAMPLE_CHARS = 80

SWITCH_OFF = "switch off"
NOT_ADDED = "not switched on yet"

_PATH_RE = re.compile(r"^\$(?:\.[A-Za-z0-9_\-:@]+|\[\*\])*$")
_IMAGE_EXT = re.compile(r"\.(?:jpe?g|png|gif|webp)(?:\?|$)", re.I)
_AUDIO_EXT = re.compile(r"\.(?:mp3|m4a|aac|ogg|opus|wav)(?:\?|$)", re.I)
_VIDEO_EXT = re.compile(r"\.(?:mp4|webm|mov)(?:\?|$)", re.I)
_HLS = re.compile(r"\.m3u8(?:\?|$)", re.I)
_YOUTUBE = re.compile(r"^https?://(?:www\.|m\.)?(?:youtube\.com/(?:watch|embed|live)|youtu\.be/)",
                      re.I)
_DATE_VALUE = re.compile(r"^\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2})?)?|"
                         r"^[A-Z][a-z]{2}, \d{1,2} [A-Z][a-z]{2} \d{4}")
_TITLE_KEYS = ("title", "name", "caption", "headline", "label", "track")
_IMAGE_KEYS = ("image", "img", "picture", "thumb", "thumbnail", "cover", "logo", "icon",
               "photo", "poster", "artwork")
_DATE_KEYS = ("date", "time", "timepoint", "published", "updated", "pubdate", "created",
              "when", "init", "timestamp")
_TEXT_KEYS = ("description", "summary", "text", "body", "content", "explanation", "about")
_LAT_KEYS = ("lat", "latitude")
_LON_KEYS = ("lon", "lng", "long", "longitude")

#: Roles a field can have, in the order the brain's list shows them.
ROLES = ("title", "image", "image_ref", "hls", "youtube", "audio", "video", "date",
         "lat", "lon", "text", "link", "number", "other")


# ── one real sample, through the Bridge ─────────────────────────────────────

#: Refusals no switch of the source's own can change.
NOT_A_SWITCH = frozenset({"bot_offline", "turned_off_here", "network_filter"})
#: What a refusal code means, in plain words (``{name}`` is the source).
REFUSAL_WORDS = {
    "bot_offline": "your bot is set to stay off the internet, so {name} was not asked",
    "switched_off": "the Bridge switch for {name} is off",
    "not_on_list": "{name} has no place on the Bridge yet",
    "turned_off_here": "outside connections are turned off on this computer, so {name} "
                       "was not asked",
    "network_filter": "your network's filter stopped the call to {name}",
    "not_encrypted": "{name} does not answer on an encrypted connection",
    "cannot_look_up": "the address of {name} could not be looked up",
}


def refusal_reason(got: dict, name: str) -> str:
    """The true reason a fetch was refused, from its refusal code."""
    code = str(got.get("code") or "")
    if got.get("reason") == NOT_ADDED and not code:
        return f"{name} is not switched on yet"
    words = REFUSAL_WORDS.get(code) or REFUSAL_WORDS["switched_off"]
    return words.format(name=name)


def fetch_sample(door: Optional[str], url: str, *, getter: Optional[Callable] = None,
                 max_bytes: int = SAMPLE_MAX_BYTES) -> dict:
    """``{"ok", "door", "url", "text"?, "switch_off"?, "reason"?}``.

    *door* is the switch the part's outside service sits behind (an app's
    live-card switch ``app:<id>:feed``, or a built-in one). None means it has
    no switch yet: nothing is fetched and the result says so."""
    out: dict = {"ok": False, "door": door or "", "url": str(url or "")}
    if not door:
        return {**out, "switch_off": True, "reason": NOT_ADDED}
    from puffpai.security import egress_doors
    get = getter or egress_doors.egress_get
    for attempt in range(FETCH_TRIES):
        got = _fetch_once(get, door, url, out, max_bytes, egress_doors)
        if not got.get("transient") or attempt == FETCH_TRIES - 1:
            return got
        import time
        time.sleep(FETCH_BACKOFF_S[min(attempt, len(FETCH_BACKOFF_S) - 1)])
    return got


def _fetch_once(get: Callable, door: str, url: str, out: dict, max_bytes: int,
                egress_doors: Any) -> dict:
    """One try. A timeout, a dropped connection or a busy answer is marked
    ``transient`` (asked again); a refusal never is."""
    try:
        resp = get(door, url, timeout=FETCH_TIMEOUT, stream=True)
    except egress_doors.EgressBlocked as exc:
        # The Bridge's own refusal code: "the bot may not go online" is not
        # "this switch is off" (said as it is, never as a switch).
        code = str(getattr(exc, "code", "") or "")
        got = {**out, "switch_off": code not in NOT_A_SWITCH, "reason": SWITCH_OFF,
               "said": str(getattr(exc, "user_message", "") or "")[:300]}
        if code:
            got["code"] = code
        return got
    except Exception as exc:  # noqa: BLE001 - a part that did not answer is a reason
        return {**out, "reason": f"it did not answer ({type(exc).__name__})", "transient": True}
    try:
        code = int(getattr(resp, "status_code", 0) or 0)
        if code != 200:
            return {**out, "reason": f"it answered {code}",
                    **({"transient": True} if code in RETRY_STATUSES else {})}
        body = _read_capped(resp, max_bytes)
    finally:
        close = getattr(resp, "close", None)
        if callable(close):
            close()
    if body is None:
        return {**out, "reason": "its answer was too big for one sample"}
    return {**out, "ok": True, "text": body.decode("utf-8", "replace")}


def _read_capped(resp: Any, max_bytes: int) -> Optional[bytes]:
    chunks, total = [], 0
    it = getattr(resp, "iter_content", None)
    pieces = it(65536) if callable(it) else [getattr(resp, "content", b"") or b""]
    for chunk in pieces:
        if not chunk:
            continue
        total += len(chunk)
        if total > max_bytes:
            return None
        chunks.append(bytes(chunk))
    return b"".join(chunks)


# ── reading a sample ────────────────────────────────────────────────────────

def parse(text: str) -> tuple[str, Any]:
    """``(kind, data)``: kind is ``json``, ``rss``, ``m3u`` or ``text``. RSS/Atom
    and stream lists come back as ``{"items": [...]}``."""
    raw = str(text or "").lstrip("﻿").strip()
    if raw.startswith("#EXTM3U"):
        return "m3u", {"items": _m3u_items(raw)}
    if raw[:1] in "[{":
        try:
            return "json", json.loads(raw)
        except ValueError:
            pass
    if raw.startswith("<"):
        items = _feed_items(raw)
        if items is not None:
            return "rss", {"items": items}
    return "text", raw


_EXTINF_ATTR = re.compile(r'([a-z][a-z0-9-]*)="([^"]*)"', re.I)


def _m3u_items(raw: str) -> list[dict]:
    items, pending = [], None
    for line in raw.splitlines():
        line = line.strip()
        if line.startswith("#EXTINF"):
            head, _, title = line.partition(",")
            attrs = {k.lower().replace("-", "_"): v for k, v in _EXTINF_ATTR.findall(head)}
            pending = {"title": title.strip(), **attrs}
        elif line and not line.startswith("#"):
            row = pending or {"title": line.rsplit("/", 1)[-1]}
            row["url"] = line
            items.append(row)
            pending = None
    return items


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _feed_items(raw: str) -> Optional[list[dict]]:
    head = raw[:2000].lower()
    if "<!doctype" in head or "<!entity" in head:
        return None
    try:
        from defusedxml import ElementTree as parser  # type: ignore[import-not-found]
    except ImportError:  # pragma: no cover - depends on the machine
        import xml.etree.ElementTree as parser  # noqa: N813
    try:
        root = parser.fromstring(raw.encode("utf-8"))
    except Exception:  # noqa: BLE001 - not a feed
        return None
    nodes = [el for el in root.iter() if _local(el.tag) in ("item", "entry")]
    if not nodes:
        return None
    items = []
    for node in nodes[:200]:
        row: dict = {}
        for child in node:
            name = _local(child.tag)
            if name == "link" and child.get("href"):
                row.setdefault("link", child.get("href"))
            elif name in ("enclosure", "content", "thumbnail") and child.get("url"):
                kind = str(child.get("type") or "")
                key = "image" if kind.startswith("image") or name == "thumbnail" else "media"
                row.setdefault(key, child.get("url"))
            elif (child.text or "").strip() and name not in row:
                row[name] = " ".join(child.text.split())
        items.append(row)
    return items


# ── paths ───────────────────────────────────────────────────────────────────

def path_ok(path: Any) -> bool:
    return isinstance(path, str) and bool(_PATH_RE.match(path))


def _steps(path: str) -> list[str]:
    return re.findall(r"\.([A-Za-z0-9_\-:@]+)|(\[\*\])", path)


def items_at(data: Any, path: str) -> list:
    """The items a simple JSONPath (``$``, ``$.a.b``, ``$[*]``, ``$.a[*]``)
    points at. A path ending in ``[*]`` gives the array's members; any other
    path gives ``[value]``. A path that does not fit gives []."""
    if not path_ok(path):
        return []
    current: list = [data]
    for key, star in _steps(path):
        nxt: list = []
        for value in current:
            if star:
                if isinstance(value, list):
                    nxt.extend(value)
            elif isinstance(value, dict) and key in value:
                nxt.append(value[key])
        current = nxt
    return current


def value_at(item: Any, field: str) -> Any:
    """A dotted field inside one item (``centroid_coordinates.lat``)."""
    value = item
    for key in str(field or "").split("."):
        if isinstance(value, dict) and key in value:
            value = value[key]
        else:
            return None
    return value


_TMPL_RE = re.compile(r"\{([A-Za-z0-9_.\-]+)(?::(year|month|day|date))?\}")


def fill_template(template: str, item: Any) -> str:
    """``tmpl:`` field: ``{key}`` and ``{key:year|month|day|date}`` from the item.
    Empty when a part is missing (an address with a hole in it is no address)."""
    missing = []

    def one(m: re.Match) -> str:
        value = value_at(item, m.group(1))
        if value is None or value == "":
            missing.append(m.group(1))
            return ""
        text = str(value)
        part = m.group(2)
        if part:
            d = re.match(r"(\d{4})-(\d{2})-(\d{2})", text)
            if not d:
                missing.append(m.group(1))
                return ""
            text = {"year": d.group(1), "month": d.group(2), "day": d.group(3),
                    "date": d.group(0)}[part]
        return text
    out = _TMPL_RE.sub(one, str(template or ""))
    return "" if missing else out


def field_of(item: Any, spec: Any) -> Any:
    """One spec field: a dotted path, or ``tmpl:...``."""
    if isinstance(spec, str) and spec.startswith("tmpl:"):
        return fill_template(spec[5:], item)
    return value_at(item, spec) if isinstance(spec, str) else None


def rows_from(data: Any, items_path: str, fields: dict, *, limit: int = 200) -> list[dict]:
    """A source's answer as rows ``{field: value}`` (the spec's ``fields``)."""
    rows = []
    for item in items_at(data, items_path)[:limit]:
        rows.append({name: field_of(item, spec) for name, spec in (fields or {}).items()})
    return rows


# ── the numbered list ───────────────────────────────────────────────────────

def _key_has(key: str, words: tuple[str, ...]) -> bool:
    k = key.lower().rsplit(".", 1)[-1]
    return any(k == w or k.startswith(w + "_") or k.endswith("_" + w) or
               (len(w) > 3 and w in k) for w in words)


def role_of(key: str, value: Any) -> str:
    """What one field is, from its name and its example value."""
    if isinstance(value, bool):
        return "other"
    if isinstance(value, (int, float)):
        if _key_has(key, _LAT_KEYS) and -90 <= value <= 90:
            return "lat"
        if _key_has(key, _LON_KEYS) and -180 <= value <= 180:
            return "lon"
        if _key_has(key, _DATE_KEYS):
            return "date"
        return "number"
    if not isinstance(value, str) or not value.strip():
        return "other"
    text = value.strip()
    if text.startswith(("http://", "https://")):
        if _HLS.search(text):
            return "hls"
        if _YOUTUBE.match(text):
            return "youtube"
        if _AUDIO_EXT.search(text) or _key_has(key, ("preview", "audio", "stream")):
            return "audio"
        if _VIDEO_EXT.search(text):
            return "video"
        if _IMAGE_EXT.search(text) or _key_has(key, _IMAGE_KEYS):
            return "image"
        return "link"
    if _key_has(key, _IMAGE_KEYS) and " " not in text:
        return "image_ref"          # a picture's name: needs an address template
    if _key_has(key, _DATE_KEYS) or _DATE_VALUE.match(text):
        return "date"
    if _key_has(key, _TITLE_KEYS):
        return "title"
    if _key_has(key, _TEXT_KEYS) or len(text) > 40:
        return "text"
    return "other"


#: Short names sources use, in plain words (any subject).
_ABBREV = {"temp": "temperature", "prec": "precipitation", "precip": "precipitation",
           "rh": "relative humidity", "lat": "latitude", "lon": "longitude",
           "lng": "longitude", "desc": "description", "img": "image", "dir": "direction",
           "qty": "quantity", "num": "number", "pct": "percent", "avg": "average",
           "min": "lowest", "max": "highest", "url": "address", "id": "id"}
#: Word endings run together in a field name ("cloudcover", "timepoint").
_JOINED = ("cover", "speed", "direction", "point", "type", "time", "date", "level",
           "index", "rate", "count", "name", "code", "size", "length", "height", "width")


def humanize(path: str) -> str:
    """A field's own name in plain words: ``cloudcover`` -> ``Cloud cover``,
    ``wind10m.direction`` -> ``Wind direction``, ``prec_type`` ->
    ``Precipitation type``. Only the source's name is used, nothing guessed."""
    words: list[str] = []
    for seg in str(path or "").split("."):
        seg = re.sub(r"([a-z])([A-Z])", r"\1 \2", seg)
        for w in re.split(r"[\s_\-:@]+", seg.lower()):
            w = re.sub(r"(?<=[a-z])\d+[a-z]{0,2}$", "", w)    # wind10m, temp2m
            if not w:
                continue
            for end in _JOINED:
                if w.endswith(end) and len(w) > len(end) + 2:
                    w = f"{w[:-len(end)]} {end}"
                    break
            for part in w.split():
                part = _ABBREV.get(part, part)
                if not words or words[-1] != part:
                    words.append(part)
    text = " ".join(words).strip()
    return (text[:1].upper() + text[1:])[:40] if text else ""


def _leaves(item: Any, prefix: str = "", depth: int = 0) -> list[tuple[str, Any]]:
    out: list[tuple[str, Any]] = []
    if not isinstance(item, dict) or depth > 2:
        return out
    for key, value in item.items():
        name = f"{prefix}{key}"
        if isinstance(value, dict):
            out.extend(_leaves(value, name + ".", depth + 1))
        elif not isinstance(value, list):
            out.append((name, value))
    return out


def _arrays(data: Any, path: str = "$", depth: int = 0) -> list[tuple[str, list]]:
    """Every list of objects in the sample, outermost first."""
    out: list[tuple[str, list]] = []
    if depth > MAX_DEPTH:
        return out
    if isinstance(data, list):
        if data and any(isinstance(v, dict) for v in data[:5]):
            out.append((path + "[*]", data))
        for v in data[:1]:
            out.extend(_arrays(v, path + "[*]", depth + 1))
    elif isinstance(data, dict):
        for key, value in data.items():
            if re.match(r"^[A-Za-z0-9_\-:@]+$", str(key)):
                out.extend(_arrays(value, f"{path}.{key}", depth + 1))
    return out


def _example(value: Any) -> str:
    text = " ".join(str(value).split())
    return text if len(text) <= EXAMPLE_CHARS else text[:EXAMPLE_CHARS - 3] + "..."


def candidates(data: Any) -> dict:
    """``{"arrays": [{"n", "path", "count", "fields": [n...]}], "fields":
    [{"n", "array", "path", "role", "example"}]}``. Field numbers run across
    the whole sample, so the brain names one field by one number."""
    arrays, fields = [], []
    for path, members in _arrays(data)[:MAX_ARRAYS]:
        dicts = [m for m in members[:10] if isinstance(m, dict)]
        seen: dict[str, tuple[str, Any]] = {}
        for member in dicts:
            for key, value in _leaves(member):
                role = role_of(key, value)
                if key not in seen or (seen[key][0] == "other" and role != "other"):
                    seen[key] = (role, value)
        arr = {"n": len(arrays) + 1, "path": path, "count": len(members), "fields": []}
        order = sorted(seen.items(), key=lambda kv: ROLES.index(kv[1][0]))
        for key, (role, value) in order:
            if len(fields) >= MAX_FIELDS:
                break
            n = len(fields) + 1
            fields.append({"n": n, "array": arr["n"], "path": key, "role": role,
                           "label": humanize(key), "example": _example(value)})
            arr["fields"].append(n)
        arrays.append(arr)
    return {"arrays": arrays, "fields": fields}


def shape(sample_text: str) -> dict:
    """``{"kind", "data", "arrays", "fields"}`` for one sample's text."""
    kind, data = parse(sample_text)
    found = candidates(data) if kind != "text" else {"arrays": [], "fields": []}
    return {"kind": kind, "data": data, **found}


def describe(found: dict, source_id: str) -> str:
    """The numbered list as the brain reads it, a few lines per array."""
    lines = []
    for arr in found.get("arrays") or []:
        lines.append(f"Source {source_id}, list {arr['n']} ({arr['count']} items):")
        for n in arr["fields"]:
            f = found["fields"][n - 1]
            lines.append(f"  {n}. {f['path']} [{f['role']}] e.g. {f['example']}")
    return "\n".join(lines) or f"Source {source_id}: no list found."


__all__ = ["NOT_ADDED", "REFUSAL_WORDS", "ROLES", "SWITCH_OFF", "refusal_reason", "candidates", "describe", "fetch_sample",
           "field_of", "fill_template", "humanize", "items_at", "parse", "path_ok", "role_of",
           "rows_from", "shape", "value_at"]
