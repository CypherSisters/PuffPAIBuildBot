"""The entries an app came with (its own examples), told apart from the owner's.

Many apps arrive full of their own demo data (a sample wedding with 18
guests, and a setup block naming a stranger's couple, date, city and
budget). At install, Toolsmith opens one fresh throwaway copy of the app,
lets it write whatever it writes on its own, and keeps it here
(``<state>/webapps/<id>/examples.json``): the records (``rows``) and the
whole fresh storage (``seed``), with the day it was kept. The bot says which
entries came with the app when it reads a list to the owner and on the app's
own page, and the owner can clear them with one tap there (:func:`clear`):

* every record that is still exactly one of the app's own goes;
* every detail outside the records (a setup or settings block: names, a
  date, a place, an amount) that is still exactly the app's own value is
  emptied, so no stranger's wedding stays at the top of her app;
* never an entry or a detail she added or changed; app settings that are
  not data (a version number, a theme word, a language code, yes/no) stay;
* the clear is a new version of the bot's copy, so "Undo" puts it all back.

An app that dates its examples from the day it is opened ("the wedding is
in 180 days") writes another date in her copy than in the throwaway one.
Only the DAY her copy was first written is kept (:func:`note_first_write`,
never what was written, which may already hold her own entry): a date of
hers is the app's own when it is the kept date moved by the same number of
days.

Before the app has written anything to her copy (its very first open), the
notice already counts what it will write, and a clear writes the app's own
fresh storage without its examples, so the app opens empty.

The kept entries are the app's own words, never the owner's; they stay on
this computer with the app's other state and go when the app is removed.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import time
from typing import Any, Iterator, Optional

from puffpai import webapps

MAX_ROWS = 500
MAX_ROW_CHARS = 4000
#: The app's whole fresh storage is kept only up to this size.
MAX_SEED_BYTES = 1024 * 1024
_DEPTH = 3
#: How many details outside the records are looked at, at most.
MAX_DETAILS = 400


def _path(app_id: str) -> str:
    return os.path.join(webapps.app_state_dir(app_id), "examples.json")


def _rows_of(value: Any, depth: int = 0) -> list[dict]:
    """Every record (a dict inside a list) in a stored value, a few levels deep."""
    out: list[dict] = []
    if depth > _DEPTH:
        return out
    if isinstance(value, list):
        for x in value:
            if isinstance(x, dict):
                out.append(x)
            elif isinstance(x, list):
                out.extend(_rows_of(x, depth + 1))
    elif isinstance(value, dict):
        for v in value.values():
            if isinstance(v, (list, dict)):
                out.extend(_rows_of(v, depth + 1))
    return out


def rows_in(storage: Any) -> list[dict]:
    """The records in an app's stored data (``{key: JSON text}``)."""
    rows: list[dict] = []
    if not isinstance(storage, dict):
        return rows
    for raw in storage.values():
        try:
            value = json.loads(raw) if isinstance(raw, str) else raw
        except ValueError:
            continue
        rows.extend(_rows_of(value))
        if len(rows) >= MAX_ROWS:
            break
    return rows[:MAX_ROWS]


def _write(app_id: str, rec: dict) -> None:
    path = _path(app_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(rec, fh, ensure_ascii=False)
    os.replace(tmp, path)


def _today() -> str:
    return time.strftime("%Y-%m-%d")


def save(app_id: str, storage: Any) -> int:
    """Keep the records and the whole storage a fresh copy of the app wrote
    by itself, with today's date. Returns how many records."""
    if not webapps.valid_app_id(app_id):
        raise ValueError("not an app id")
    kept = []
    for r in rows_in(storage):
        text = json.dumps(r, sort_keys=True, ensure_ascii=False)
        if len(text) <= MAX_ROW_CHARS:
            kept.append(r)
    seed = {k: v for k, v in storage.items() if isinstance(k, str) and isinstance(v, str)} \
        if isinstance(storage, dict) else {}
    if len(json.dumps(seed, ensure_ascii=False).encode("utf-8")) > MAX_SEED_BYTES:
        seed = {}
    _write(app_id, {"rows": kept, "seed": seed, "at": _today()})
    return len(kept)


def _record(app_id: str) -> dict:
    if not webapps.valid_app_id(app_id):
        return {}
    try:
        with open(_path(app_id), encoding="utf-8") as fh:
            got = json.load(fh)
    except (OSError, ValueError):
        return {}
    return got if isinstance(got, dict) else {}


def recorded(app_id: str) -> list[dict]:
    rows = _record(app_id).get("rows")
    return [r for r in rows if isinstance(r, dict)][:MAX_ROWS] if isinstance(rows, list) else []


def _seed(rec: dict) -> dict:
    seed = rec.get("seed")
    return {k: v for k, v in seed.items() if isinstance(k, str) and isinstance(v, str)} \
        if isinstance(seed, dict) else {}


def note_first_write(app_id: str) -> None:
    """Her copy of the app was written for the first time today: keep the
    day (only the day, never what was written). Called by the app's host
    after the write that made version 1."""
    rec = _record(app_id)
    if not rec or rec.get("first_at"):
        return
    rec["first_at"] = _today()
    try:
        _write(app_id, rec)
    except OSError:
        pass


def _first_at(app_id: str, rec: dict) -> Optional[str]:
    got = rec.get("first_at")
    if isinstance(got, str):
        return got
    # a copy first written before this was kept: the day of its version 1
    try:
        with open(os.path.join(webapps.app_state_dir(app_id), "versions", "1.json"),
                  encoding="utf-8") as fh:
            v1 = json.load(fh)
    except (OSError, ValueError):
        return None
    at = v1.get("at") if isinstance(v1, dict) else None
    return at[:10] if isinstance(at, str) else None


def _day(text: Any) -> Optional[_dt.date]:
    if not isinstance(text, str) or not re.match(r"^\d{4}-\d{2}-\d{2}", text):
        return None
    try:
        return _dt.date.fromisoformat(text[:10])
    except ValueError:
        return None


def _shifts(app_id: str, rec: dict) -> frozenset:
    """The day counts a date of the app's own may have moved by in her copy:
    0, and the days from the throwaway copy to her copy's first write (one
    day either way, as an app may count in UTC)."""
    a, b = _day(rec.get("at")), _day(_first_at(app_id, rec))
    if a is None or b is None:
        return frozenset({0})
    d = (b - a).days
    return frozenset({0, d - 1, d, d + 1}) if 0 <= d <= 3660 else frozenset({0})


def _same(example: Any, value: Any, shifts: frozenset) -> bool:
    """The app's own value: the same, or the same date moved by one of
    ``shifts`` days (the rest of a date and time the same)."""
    if example == value:
        return True
    de, dv = _day(example), _day(value)
    if de is None or dv is None or example[10:] != value[10:]:
        return False
    return (dv - de).days in shifts


#: Fields an app makes up anew for each copy (an id, a time it was made):
#: they never tell an example from the owner's entry, so they are not compared.
_MADE_UP_NAME = re.compile(r"^_?(?:id|uuid|guid|key)$|[a-z0-9](?:Id|ID|Uuid|UUID|Guid)$|_(?:id|uuid|guid)$"
                           r"|^(?:created|updated|modified|added)(?:_?(?:at|on|date|time))?$"
                           r"|[a-z](?:At|On)$")          # case matters: "paid" is not an id
_ISO_TIME = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")


def _made_up(key: str, value: Any) -> bool:
    if _MADE_UP_NAME.search(str(key)) or str(key).lower() in ("id", "uuid", "guid", "_id"):
        return True
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 1e11:
        return True                       # a time in milliseconds
    return isinstance(value, str) and _ISO_TIME.match(value) is not None


def _is_example(row: Any, examples: list[dict], shifts: frozenset = frozenset({0})) -> bool:
    """True when every field of ``row`` (a whole record, or the fields a read
    picked out of one) is the same in one of the app's own records, leaving
    out fields the app makes up anew for each copy (ids, times); a date may
    have moved by one of ``shifts`` days. At least one compared field must
    hold something."""
    if not isinstance(row, dict):
        return False
    keys = [k for k, v in row.items() if not _made_up(k, v)]
    if not any(row[k] not in (None, "", False) for k in keys):
        return False
    return any(all(_same(e.get(k), row[k], shifts) for k in keys) for e in examples)


def count_in(app_id: str, rows: Any) -> int:
    """How many of ``rows`` (what a read returned) came with the app."""
    examples = recorded(app_id)
    if not examples or not isinstance(rows, list):
        return 0
    shifts = _shifts(app_id, _record(app_id))
    return sum(1 for r in rows if _is_example(r, examples, shifts))


# -- details outside the records (a setup or settings block) --------------------

#: Words of a setting's name that say it is how the app works, not her data
#: (a number there is never emptied: "version": 3, "fontSize": 14).
_SETTING_WORDS = frozenset("""
version ver schema rev revision theme mode lang language locale currency unit units format
tab tabs view sort order filter layout color colour palette setting settings pref prefs
preference preferences onboard onboarded onboarding seen tour flag flags enabled disabled step
page zoom scale font size width height dark light accent density launch launches install
installed migrated migration count index idx level open opened collapsed expanded visible
""".split())
#: Values that are how the app works, not her data: a lower-case word or
#: code ("dark", "grid", "en"), a language ("en-US"), a currency ("USD"), a
#: colour ("#ff8800"), a time zone ("America/New_York"), a web address or path.
_SETTING_VALUE = re.compile(
    r"^(?:[a-z][a-z0-9_.-]{0,23}|[a-z]{2,3}(?:[-_][A-Za-z0-9]{2,8})+|[A-Z]{3}|"
    r"#[0-9a-fA-F]{3,8}|[A-Za-z_]+/[A-Za-z0-9_/+-]+|(?:https?:|/|\./|blob:|data:).*)$", re.S)


def _name_words(name: str) -> set[str]:
    t = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", str(name))
    return {w for w in re.split(r"[^A-Za-z0-9]+", t.lower()) if w}


def _detail_value(name: str, value: Any) -> bool:
    """True when ``value`` (under the setting ``name``) can be someone's data
    (a name, a place, a date, an amount), so an app's own one is emptied."""
    if value is None or isinstance(value, bool):
        return False
    if _MADE_UP_NAME.search(str(name)) or str(name).lower() in ("id", "uuid", "guid", "_id"):
        return False
    if isinstance(value, (int, float)):
        return value != 0 and value < 1e11 and not (_name_words(name) & _SETTING_WORDS)
    if isinstance(value, str):
        return bool(value.strip()) and not _SETTING_VALUE.match(value.strip())
    if isinstance(value, list):
        return bool(value) and all(isinstance(x, (str, int, float)) and not isinstance(x, bool)
                                   for x in value) \
            and any(isinstance(x, str) and _detail_value(name, x) for x in value)
    return False


def _empty(value: Any) -> Any:
    if isinstance(value, str):
        return ""
    if isinstance(value, (int, float)):
        return 0
    return []


def _details(value: Any, path: tuple = (), depth: int = 0) -> Iterator[tuple[tuple, Any]]:
    """(path, value) of every detail reached through dicts only (never inside
    a list of records), a few levels deep."""
    if depth > _DEPTH:
        return
    if isinstance(value, dict):
        for k, v in value.items():
            if isinstance(v, dict):
                yield from _details(v, path + (k,), depth + 1)
            elif not (isinstance(v, list) and any(isinstance(x, (dict, list)) for x in v)):
                yield path + (k,), v
    elif not isinstance(value, list):
        yield path, value


def _parse(raw: Any) -> tuple[Any, bool]:
    """A stored value read as JSON, and whether it was JSON at all."""
    if not isinstance(raw, str):
        return raw, False
    try:
        return json.loads(raw), True
    except ValueError:
        return raw, False


def _seed_details(seed: dict) -> dict[tuple, Any]:
    out: dict[tuple, Any] = {}
    for key, raw in seed.items():
        value, _ = _parse(raw)
        for path, v in _details(value):
            name = path[-1] if path else key
            if _detail_value(str(name), v):
                out[(key,) + path] = v
            if len(out) >= MAX_DETAILS:
                return out
    return out


def _get(value: Any, path: tuple) -> Any:
    for p in path:
        if not isinstance(value, dict) or p not in value:
            return _MISSING
        value = value[p]
    return value


def _set(value: Any, path: tuple, new: Any) -> Any:
    if not path:
        return new
    out = dict(value)
    out[path[0]] = _set(value[path[0]], path[1:], new)
    return out


_MISSING = object()


def _still_own(cur: Any, own: Any, shifts: frozenset) -> bool:
    if cur is _MISSING or isinstance(cur, bool) or cur in (None, "", 0, []):
        return False
    if isinstance(own, list) or isinstance(cur, list):
        return cur == own
    return type(cur) is type(own) and _same(own, cur, shifts) if isinstance(own, str) \
        else cur == own


def _without_details(data: dict, own: dict[tuple, Any], shifts: frozenset) -> tuple[dict, int]:
    """``data`` with every detail that is still exactly the app's own emptied."""
    out = dict(data)
    n = 0
    by_key: dict[str, list[tuple[tuple, Any]]] = {}
    for full, v in own.items():
        by_key.setdefault(full[0], []).append((full[1:], v))
    for key, items in by_key.items():
        if key not in out:
            continue
        value, was_json = _parse(out[key])
        changed = 0
        for path, v in items:
            cur = _get(value, path)
            if _still_own(cur, v, shifts):
                value = _set(value, path, _empty(cur))
                changed += 1
        if changed:
            n += changed
            out[key] = json.dumps(value, ensure_ascii=False, separators=(",", ":")) \
                if was_json else value
    return out, n


def _details_left(data: dict, own: dict[tuple, Any], shifts: frozenset) -> int:
    n = 0
    for full, v in own.items():
        if full[0] not in data:
            continue
        value, _ = _parse(data[full[0]])
        if _still_own(_get(value, full[1:]), v, shifts):
            n += 1
    return n


def left(app_id: str) -> tuple[int, int]:
    """(records, details) of the app's own still in the bot's copy of it.
    Before the app has written anything to her copy (its first open): what
    it will write, from the throwaway copy."""
    rec = _record(app_id)
    examples = recorded(app_id)
    own = _seed_details(_seed(rec))
    if not examples and not own:
        return 0, 0
    from puffpai.webapps import mirror
    try:
        cur = mirror.get(app_id)
    except (ValueError, OSError):
        return 0, 0
    if not cur["v"] and not cur["data"]:
        return len(examples), len(own)
    shifts = _shifts(app_id, rec)
    rows = sum(1 for r in rows_in(cur["data"]) if _is_example(r, examples, shifts)) \
        if examples else 0
    return rows, _details_left(cur["data"], own, shifts)


def still_there(app_id: str) -> int:
    """How many of the app's own records are still in the bot's copy of it."""
    return left(app_id)[0]


def _without_examples(value: Any, examples: list[dict], shifts: frozenset,
                      depth: int = 0) -> tuple[Any, int]:
    """``value`` with every example record left out of its lists, and how many."""
    if depth > _DEPTH:
        return value, 0
    gone = 0
    if isinstance(value, list):
        out = []
        for x in value:
            if isinstance(x, dict) and _is_example(x, examples, shifts):
                gone += 1
                continue
            if isinstance(x, list):
                x, n = _without_examples(x, examples, shifts, depth + 1)
                gone += n
            out.append(x)
        return out, gone
    if isinstance(value, dict):
        out_d = {}
        for k, v in value.items():
            if isinstance(v, (list, dict)):
                v, n = _without_examples(v, examples, shifts, depth + 1)
                gone += n
            out_d[k] = v
        return out_d, gone
    return value, 0


def clear(app_id: str) -> dict:
    """Take the app's own example records out of the bot's copy of its data
    (what the owner's page loads) and empty every detail that is still the
    app's own, leaving every other entry and detail as it is.

    ``{"ok", "removed", "details", "version", "why"}``. Only a stored value
    that held an example is written again (as compact JSON, the way apps
    write it); the rest stays byte for byte. Refused while the bot is using
    the app. The change is one new version, so the page's "Undo"
    (``mirror.restore``) puts it all back. Before the app has written
    anything to her copy, its own fresh storage is written without its
    examples (so its first open shows none); with nothing kept from the
    throwaway copy, ``why`` is ``"not_opened"``."""
    if not webapps.valid_app_id(app_id):
        return {"ok": False, "removed": 0, "details": 0, "version": 0, "why": "not_an_app"}
    rec = _record(app_id)
    examples = recorded(app_id)
    own = _seed_details(_seed(rec))
    from puffpai.webapps import mirror
    cur = mirror.get(app_id)
    if not examples and not own:
        return {"ok": True, "removed": 0, "details": 0, "version": cur["v"], "why": ""}
    base = cur["data"]
    if not cur["v"] and not base:
        base = _seed(rec)
        if not base:
            return {"ok": False, "removed": 0, "details": 0, "version": 0, "why": "not_opened"}
    shifts = _shifts(app_id, rec)
    data = dict(base)
    removed = 0
    for key, raw in base.items():
        try:
            value = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if not isinstance(value, (list, dict)):
            continue
        kept, n = _without_examples(value, examples, shifts)
        if n:
            removed += n
            data[key] = json.dumps(kept, ensure_ascii=False, separators=(",", ":"))
    data, details = _without_details(data, own, shifts)
    if not removed and not details:
        return {"ok": True, "removed": 0, "details": 0, "version": cur["v"], "why": ""}
    status, body = mirror.put(app_id, cur["v"], data)
    if status != 200:
        return {"ok": False, "removed": 0, "details": 0, "version": cur["v"],
                "why": "busy" if status == 423 else "changed"}
    return {"ok": True, "removed": removed, "details": details, "version": int(body["v"]),
            "why": ""}


__all__ = ["save", "recorded", "count_in", "still_there", "left", "rows_in", "clear",
           "note_first_write"]
