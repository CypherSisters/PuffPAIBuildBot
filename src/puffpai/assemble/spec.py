"""The ``assembled_app/v1`` spec: what a put-together app is, and how it is made.

The spec is the contract between the Assemble parts: this module makes and
checks it, the app server serves it, the scaffold draws its views, the proof
calls its actions. Shape::

    {"schema": "assembled_app/v1", "id": "space_pictures", "title": "...",
     "caps": ["cap-..."],
     "settings": [{"key": "home_town", "kind": "place", "use": "home_location"}],
     "sources": [{"id": "pics", "kind": "http_json", "service": "nasa_epic",
                  "door": "app:nasa_epic:feed", "url": "https://...",
                  "items": "$[*]", "fields": {"title": "caption",
                  "image": "tmpl:https://.../{date:year}/{image}.jpg"},
                  "args": {"lat": "setting:home_town.lat"}},
                 {"id": "wx", "kind": "glue", "module": "glue/wx.py", "fn": "forecast"}],
     "views": [{"type": "gallery", "source": "pics", "search": true, "lightbox": true}],
     "actions": [{"id": "search_pics", "source": "pics", "input": {"q": "string"}}],
     "checks": [{"cap": "cap-...", "ask": "Show me pictures of Earth",
                 "action": "search_pics", "input": {"q": "earth"},
                 "expect": {"min_items": 1, "fields": ["image"]}}]}

How a spec is made (:func:`build`): the small brain never writes the spec. It
picks from numbered lists (the view types, the candidate fields from
:mod:`.shape`, the capabilities) and says one sample question per capability;
:func:`from_picks` turns the picks into the spec and :func:`validate` checks
it. A wrong answer gets the validator's words back, up to two times; then the
fixed fallback is used (first item list: a gallery when it has pictures, else
a table), so a build always ends with an app that can be proven or plainly
not.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable, Optional

from puffpai.assemble import APP_ID_RE, SPEC_SCHEMA, shape
from puffpai.logging_config import get_logger

logger = get_logger(__name__)

VIEWS = ("gallery", "table", "forecast", "map", "player", "chart", "list", "form",
         "settings", "links")
SETTING_KINDS = ("place", "text", "choice", "key")
SOURCE_KINDS = ("http_json", "rss", "m3u", "glue", "local_app")
PLAYER_MODES = ("audio", "video", "hls", "youtube")
INPUT_TYPES = ("string", "number", "integer", "boolean")
FIELD_NAMES = ("title", "image", "when", "text", "lat", "lon", "stream", "link", "value")
REPAIR_ROUNDS = 2
MAX_LIST = 20
#: More of a source's own fields shown as their own columns (named by the source).
MORE_MAX = 10
LABEL_MAX = 40
MEANS_MAX = 160

_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
_CAP_RE = re.compile(r"^cap-[a-z0-9-]{1,60}$")
_SETTING_REF = re.compile(r"^setting:([a-z][a-z0-9_]{0,39})(?:\.(lat|lon|name))?$")
_PLAIN_RE = re.compile(r"^[\w\s.,;:%()/'+\-\u00b0]+$")

#: What a capability's own name asks the app to show (any subject).
CAP_VIEWS = (("gallery", re.compile(r"\b(pictures?|images?|photos?|photographs?|gallery|"
                                    r"artworks?|wallpapers?)\b", re.I)),
             ("map", re.compile(r"\b(maps?|positions?)\b", re.I)),
             ("forecast", re.compile(r"\bforecasts?\b", re.I)),
             ("player", re.compile(r"\b(play|playing|listen|listening|radio|streams?|"
                                   r"podcasts?)\b", re.I)))
#: The same, in her words, for a reason.
VIEW_NEEDS = {"gallery": "pictures", "map": "positions on a map", "forecast": "a forecast",
              "player": "something to play"}


def plain_text(value: Any, limit: int) -> str:
    """A short plain label or meaning the brain wrote, or "" when it is not
    one (too long, an address, markup)."""
    text = re.sub("\\s*[\u2014\u2013]\\s*", ", ", " ".join(str(value or "").split()))
    if not text or len(text) > limit or "http" in text.lower() or not _PLAIN_RE.match(text):
        return ""
    return text


def cap_views(cap: dict) -> list[str]:
    """The views a capability's name asks for (``Show pictures of ...`` ->
    gallery). [] when its words ask for nothing in particular."""
    name = str((cap or {}).get("name") or "")
    return [view for view, rx in CAP_VIEWS if rx.search(name)]


def ask_for(cap: dict) -> str:
    """The proof's question, made from the capability's own words."""
    name = " ".join(str((cap or {}).get("name") or "").split()).rstrip(".")
    if not name:
        return "What can you show me?"
    first = name.split()[0].lower()
    if first in ("show", "display", "find", "give", "tell", "list", "play", "check", "get",
                 "provide", "notify", "update", "track", "keep", "alert", "read", "search"):
        return name + "."
    return f"Show me {name[:1].lower() + name[1:]}."

#: The spec as a JSON schema, for anyone who wants to read it. :func:`validate`
#: is the check that counts (it also checks references between parts).
SCHEMA: dict = {
    "type": "object",
    "required": ["schema", "id", "title", "caps", "sources", "views", "actions", "checks"],
    "properties": {
        "schema": {"const": SPEC_SCHEMA},
        "id": {"type": "string", "pattern": APP_ID_RE.pattern},
        "title": {"type": "string", "maxLength": 60},
        "caps": {"type": "array", "items": {"type": "string", "pattern": _CAP_RE.pattern}},
        "settings": {"type": "array", "items": {
            "type": "object", "required": ["key", "kind"],
            "properties": {"key": {"type": "string"}, "kind": {"enum": list(SETTING_KINDS)},
                           "use": {"type": "string"}, "label": {"type": "string"},
                           "choices": {"type": "array", "items": {"type": "string"}}}}},
        "sources": {"type": "array", "items": {
            "type": "object", "required": ["id", "kind"],
            "properties": {"id": {"type": "string"}, "kind": {"enum": list(SOURCE_KINDS)},
                           "service": {"type": "string"}, "door": {"type": "string"},
                           "url": {"type": "string"}, "items": {"type": "string"},
                           "fields": {"type": "object"}, "args": {"type": "object"},
                           "module": {"type": "string"}, "fn": {"type": "string"},
                           "app": {"type": "string"}, "path": {"type": "string"},
                           "name": {"type": "string"},
                           "labels": {"type": "object"}, "meanings": {"type": "object"}}}},
        "views": {"type": "array", "items": {
            "type": "object", "required": ["type", "source"],
            "properties": {"type": {"enum": list(VIEWS)}, "source": {"type": "string"},
                           "search": {"type": "boolean"}, "lightbox": {"type": "boolean"},
                           "mode": {"enum": list(PLAYER_MODES)},
                           "labels": {"type": "object"}, "meanings": {"type": "object"}}}},
        "actions": {"type": "array", "items": {
            "type": "object", "required": ["id", "source"],
            "properties": {"id": {"type": "string"}, "source": {"type": "string"},
                           "input": {"type": "object"}}}},
        "checks": {"type": "array", "items": {
            "type": "object", "required": ["cap", "ask", "action", "expect"],
            "properties": {"cap": {"type": "string"}, "ask": {"type": "string"},
                           "action": {"type": "string"}, "input": {"type": "object"},
                           "expect": {"type": "object"}}}},
    },
}


# ── checking a spec ─────────────────────────────────────────────────────────

def _list(spec: dict, key: str, errors: list, *, need: bool = True) -> list:
    value = spec.get(key, None if need else [])
    if not isinstance(value, list):
        errors.append(f"{key} must be a list")
        return []
    if len(value) > MAX_LIST:
        errors.append(f"{key} has more than {MAX_LIST} entries")
    if not all(isinstance(v, dict) for v in value):
        errors.append(f"{key} entries must be objects")
    return [v for v in value if isinstance(v, dict)]


def validate(spec: Any) -> list[str]:
    """Plain reasons the spec is wrong; [] when it is usable."""
    if not isinstance(spec, dict):
        return ["the spec must be an object"]
    errors: list[str] = []
    if spec.get("schema") != SPEC_SCHEMA:
        errors.append(f"schema must be {SPEC_SCHEMA}")
    if not APP_ID_RE.match(str(spec.get("id") or "")):
        errors.append("id must be lower case letters, digits and _ (2 to 32), starting with a letter")
    title = spec.get("title")
    if not isinstance(title, str) or not title.strip() or len(title) > 60:
        errors.append("title must be a short name (1 to 60 letters)")
    caps = spec.get("caps")
    if not isinstance(caps, list) or not caps or not all(
            isinstance(c, str) and _CAP_RE.match(c) for c in caps):
        errors.append("caps must list capability ids like cap-show-pictures")
        caps = []

    settings = _list(spec, "settings", errors, need=False)
    setting_keys = set()
    for i, s in enumerate(settings):
        if not _KEY_RE.match(str(s.get("key") or "")):
            errors.append(f"settings[{i}].key is not a usable name")
        if s.get("kind") not in SETTING_KINDS:
            errors.append(f"settings[{i}].kind must be one of {', '.join(SETTING_KINDS)}")
        if s.get("kind") == "choice" and not (isinstance(s.get("choices"), list) and s["choices"]):
            errors.append(f"settings[{i}] is a choice with no choices")
        setting_keys.add(s.get("key"))

    sources = _list(spec, "sources", errors)
    source_ids = set()
    for i, s in enumerate(sources):
        sid, kind = s.get("id"), s.get("kind")
        if not _KEY_RE.match(str(sid or "")):
            errors.append(f"sources[{i}].id is not a usable name")
        elif sid in source_ids:
            errors.append(f"sources[{i}].id {sid} is used twice")
        source_ids.add(sid)
        if kind not in SOURCE_KINDS:
            errors.append(f"sources[{i}].kind must be one of {', '.join(SOURCE_KINDS)}")
        if kind in ("http_json", "rss", "m3u"):
            if not str(s.get("url") or "").startswith("https://"):
                errors.append(f"sources[{i}].url must be an https address")
            if not shape.path_ok(s.get("items")):
                errors.append(f"sources[{i}].items must be a path like $.list[*]")
            fields = s.get("fields")
            if not isinstance(fields, dict) or not fields:
                errors.append(f"sources[{i}].fields must name at least one field")
            else:
                more = [n for n in fields if n not in FIELD_NAMES]
                if len(more) > MORE_MAX:
                    errors.append(f"sources[{i}].fields has more than {MORE_MAX} more fields")
                for name, ref in fields.items():
                    if name not in FIELD_NAMES and not _KEY_RE.match(str(name)):
                        errors.append(f"sources[{i}].fields.{name} is not one of "
                                      f"{', '.join(FIELD_NAMES)} or a plain name")
                    if not isinstance(ref, str) or not ref.strip():
                        errors.append(f"sources[{i}].fields.{name} must be a field path")
        errors.extend(_words_problems(s, f"sources[{i}]", s.get("fields")))
        if kind == "glue" and not (str(s.get("module") or "").startswith("glue/")
                                   and _KEY_RE.match(str(s.get("fn") or ""))):
            errors.append(f"sources[{i}] is glue and needs module glue/<file>.py and fn")
        if kind == "local_app" and not (s.get("app") and str(s.get("path") or "").startswith("/")):
            errors.append(f"sources[{i}] is local_app and needs app and path")
        args = s.get("args")
        if args is not None and not isinstance(args, dict):
            errors.append(f"sources[{i}].args must be an object")
        for name, value in (args or {}).items() if isinstance(args, dict) else ():
            m = _SETTING_REF.match(str(value)) if isinstance(value, str) and \
                value.startswith("setting:") else None
            if isinstance(value, str) and value.startswith("setting:") and (
                    m is None or m.group(1) not in setting_keys):
                errors.append(f"sources[{i}].args.{name} names a setting that is not listed")

    views = _list(spec, "views", errors)
    if not views:
        errors.append("views must have at least one view")
    for i, v in enumerate(views):
        errors.extend(_words_problems(v, f"views[{i}]", None))
        if v.get("type") not in VIEWS:
            errors.append(f"views[{i}].type must be one of {', '.join(VIEWS)}")
        if v.get("type") != "settings" and v.get("source") not in source_ids:
            errors.append(f"views[{i}].source must be one of the sources")
        if v.get("type") == "player" and v.get("mode") not in PLAYER_MODES:
            errors.append(f"views[{i}] is a player and needs mode {', '.join(PLAYER_MODES)}")

    actions = _list(spec, "actions", errors)
    action_ids = set()
    for i, a in enumerate(actions):
        if not _KEY_RE.match(str(a.get("id") or "")):
            errors.append(f"actions[{i}].id is not a usable name")
        action_ids.add(a.get("id"))
        if a.get("source") not in source_ids:
            errors.append(f"actions[{i}].source must be one of the sources")
        inp = a.get("input", {})
        if not isinstance(inp, dict) or not all(
                _KEY_RE.match(str(k)) and t in INPUT_TYPES for k, t in inp.items()):
            errors.append(f"actions[{i}].input must map names to {', '.join(INPUT_TYPES)}")

    checks = _list(spec, "checks", errors)
    for i, c in enumerate(checks):
        if c.get("cap") not in caps:
            errors.append(f"checks[{i}].cap must be one of caps")
        if not isinstance(c.get("ask"), str) or not c["ask"].strip():
            errors.append(f"checks[{i}].ask must be a question")
        if c.get("action") not in action_ids:
            errors.append(f"checks[{i}].action must be one of the actions")
        if c.get("input") is not None and not isinstance(c.get("input"), dict):
            errors.append(f"checks[{i}].input must be an object")
        expect = c.get("expect")
        if not isinstance(expect, dict) or not isinstance(expect.get("min_items", 0), int) \
                or not isinstance(expect.get("fields", []), list):
            errors.append(f"checks[{i}].expect needs min_items (a number) and fields (a list)")
    for cap in caps:
        if not any(c.get("cap") == cap for c in checks):
            errors.append(f"{cap} has no check")
    words = spec.get("cap_words")
    if words is not None and not (isinstance(words, dict) and all(
            isinstance(v, str) and len(v) <= 300 for v in words.values())):
        errors.append("cap_words must map capability ids to their words")
    return errors


def _words_problems(obj: dict, where: str, fields: Any) -> list[str]:
    """``labels`` and ``meanings``: field name -> a short plain string."""
    out = []
    for key, limit in (("labels", LABEL_MAX), ("meanings", MEANS_MAX)):
        got = obj.get(key)
        if got is None:
            continue
        if not isinstance(got, dict) or len(got) > MAX_LIST:
            out.append(f"{where}.{key} must map field names to short words")
            continue
        for name, text in got.items():
            if isinstance(fields, dict) and name not in fields:
                out.append(f"{where}.{key}.{name} is not one of its fields")
            elif not isinstance(text, str) or plain_text(text, limit) != text:
                out.append(f"{where}.{key}.{name} must be short plain words")
    return out


# ── settings ────────────────────────────────────────────────────────────────

def _settings_path(app_id: str) -> str:
    import os
    from puffpai.assemble import app_dir
    return os.path.join(app_dir(app_id), "settings.json")


def setting_values(app_id: str) -> dict:
    from puffpai import fsutil
    got = fsutil.read_json(_settings_path(app_id), default={})
    return got if isinstance(got, dict) else {}


def save_setting(app_id: str, key: str, value: Any) -> dict:
    """Keep one setting's value. A place keeps only a rough position (one
    decimal, about 11 km) and its name; a key is never kept here (the vault
    holds keys), so a ``key`` setting is refused."""
    from puffpai import fsutil
    if not _KEY_RE.match(str(key or "")):
        raise ValueError("not a setting name")
    if isinstance(value, dict) and {"lat", "lon"} <= set(value):
        value = {"lat": round(float(value["lat"]), 1), "lon": round(float(value["lon"]), 1),
                 "name": " ".join(str(value.get("name") or "").split())[:80]}
    elif not isinstance(value, (str, int, float, bool)):
        raise ValueError("a setting is a word, a number or a place")
    values = setting_values(app_id)
    values[key] = value
    fsutil.atomic_write_json(_settings_path(app_id), values)
    return values


def resolve_place(app_id: str, setting: dict) -> Optional[dict]:
    """``{"lat", "lon", "name"}`` for a place setting, or None when unset.
    ``use: home_location`` reads the bot's own home town and nothing else (one
    place for the whole bot; saving it from an app or a chat card writes that
    same store, :mod:`puffpai.home_location`); otherwise the app's own saved
    value."""
    if setting.get("use") == "home_location":
        try:
            from puffpai import home_location
            home = home_location.get_home()
        except Exception:  # noqa: BLE001 - no home is unset, never a guess
            home = None
        if home:
            return {"lat": home.get("latitude"), "lon": home.get("longitude"),
                    "name": str(home.get("label") or "")}
        return None
    got = setting_values(app_id).get(str(setting.get("key") or ""))
    return got if isinstance(got, dict) and {"lat", "lon"} <= set(got) else None


def resolve_args(app_id: str, spec: dict, args: Optional[dict]) -> tuple[dict, list[str]]:
    """A source's ``args`` with every ``setting:<key>[.lat|.lon|.name]`` filled.
    Returns ``(args, missing setting keys)``; a missing one is left out."""
    by_key = {s.get("key"): s for s in spec.get("settings") or [] if isinstance(s, dict)}
    out, missing = {}, []
    for name, value in (args or {}).items():
        m = _SETTING_REF.match(value) if isinstance(value, str) else None
        if not m:
            out[name] = value
            continue
        setting = by_key.get(m.group(1)) or {}
        if setting.get("kind") == "place":
            place = resolve_place(app_id, setting)
            if place is None or not m.group(2):
                missing.append(m.group(1))
                continue
            out[name] = place.get(m.group(2))
        else:
            got = setting_values(app_id).get(m.group(1))
            if got is None:
                missing.append(m.group(1))
                continue
            out[name] = got
    return out, sorted(set(missing))


# ── making a spec: the brain picks, this module writes ──────────────────────

PICKS_FORMAT: dict = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "views": {"type": "array", "items": {"type": "object", "properties": {
            "view": {"type": "integer"}, "source": {"type": "string"},
            "list": {"type": "integer"}}}},
        "fields": {"type": "object"},
        "labels": {"type": "array", "items": {"type": "object", "properties": {
            "source": {"type": "string"}, "field": {"type": "integer"},
            "label": {"type": "string"}, "means": {"type": "string"}}}},
        "search": {"type": "array", "items": {"type": "string"}},
        "checks": {"type": "array", "items": {"type": "object", "properties": {
            "cap": {"type": "integer"}, "source": {"type": "string"},
            "ask": {"type": "string"}, "q": {"type": "string"}}}},
    },
    "required": ["views", "fields", "checks"],
}

PROMPT = """You put together a small app from data that was already found.
Pick ONLY from the numbered lists below. Answer with JSON only:
{{"title": "a short app name",
 "views": [{{"view": <view number>, "source": "<source id>", "list": <list number>}}],
 "fields": {{"<source id>": {{"title": <field number>, "image": <field number>,
   "when": <field number>, "text": <field number>, "lat": <field number>,
   "lon": <field number>, "stream": <field number>, "link": <field number>,
   "value": <field number>, "more": [<field number>, ...]}}}},
 "labels": [{{"source": "<source id>", "field": <field number>,
   "label": "2 to 4 plain words", "means": "what its values mean, or empty"}}],
 "search": ["<source id that can be searched by a word>"],
 "checks": [{{"cap": <capability number>, "source": "<source id>",
   "ask": "one short question the owner would ask", "q": "one search word or empty"}}]}}
Leave out any field that does not fit. "more" lists other useful fields of the
same list, each shown as its own column (a reading, a measure, a kind).
Give every field you picked a short label in plain words, from its name and from
what the source says about it. Fill "means" only when the source says what its
values mean (for example a scale like "1 clear to 9 overcast").
Every capability needs one check, and its ask must be about that capability.
Field numbers must come from the list you picked for that source.
A forecast view needs a time field and readings in "more". A map needs lat and lon.
A gallery needs a picture field. A player needs a stream field.

Views:
{views}

Capabilities:
{caps}

Sources and their fields:
{fields}
"""


def _numbered(items: list[str]) -> str:
    return "\n".join(f"{i}. {v}" for i, v in enumerate(items, 1))


def _about(part: dict) -> str:
    """What the source says about itself (its list row, its card, its README)."""
    text = " ".join(str(part.get("about") or "").split())
    text = re.sub("\\s*[\u2014\u2013]\\s*", ", ", text)
    return text[:600]


def prompt(caps: list[dict], parts: list[dict]) -> str:
    blocks = []
    for p in parts:
        about = _about(p)
        head = f"Source {p['source_id']}" + (f" ({p['name']})" if p.get("name") else "")
        means = "; ".join(f"{k}: {v}" for k, v in (p.get("meanings") or {}).items())
        blocks.append((f"{head} says: {about}\n" if about else "")
                      + (f"Its documents say what values mean: {means}\n" if means else "")
                      + shape.describe(p["found"], p["source_id"]))
    return PROMPT.format(
        views=_numbered(list(VIEWS)),
        caps=_numbered([f"{c['name']}: {c.get('does') or ''}".strip(": ") for c in caps]),
        fields="\n".join(blocks))


def _field_role_ok(name: str, role: str) -> bool:
    allowed = {"title": ("title", "text", "other", "link"),
               "image": ("image", "image_ref"), "when": ("date",),
               "text": ("text", "title", "other"), "lat": ("lat",),
               "lon": ("lon",), "stream": ("hls", "youtube", "audio", "video"),
               "link": ("link", "youtube", "hls", "audio", "video", "image"),
               "value": ("number", "other", "text", "title", "date")}
    return role in allowed.get(name, ())


def _mode_of(role: str) -> str:
    return {"hls": "hls", "youtube": "youtube", "audio": "audio", "video": "video"}.get(role, "")


_PLACE_HOLES = re.compile(r"\{(lat|lon|lat_round|lon_round)\}")


def more_key(path: str, taken: set) -> str:
    """A column's name from the source's own field name (``wind10m.direction``
    -> ``wind10m_direction``), never one of the fixed names."""
    key = re.sub(r"[^a-z0-9]+", "_", str(path or "").lower()).strip("_")[:36] or "field"
    if not key[0].isalpha():
        key = f"f_{key}"[:36]
    base, n = key, 2
    while key in taken or key in FIELD_NAMES:
        key, n = f"{base}_{n}", n + 1
    return key


def _source_of(part: dict, list_n: int, picked: dict,
               words: Optional[dict] = None) -> tuple[dict, list[str]]:
    """One spec source from a part, its picked list, its picked fields and
    the labels the brain wrote (*words*: field number -> {"label", "means"})."""
    found = part["found"]
    errors: list[str] = []
    arrays = found.get("arrays") or []
    if not 1 <= list_n <= len(arrays):
        return {}, [f"source {part['source_id']}: list {list_n} is not on its list "
                    f"(1 to {len(arrays)})"]
    arr = arrays[list_n - 1]
    words = words or {}
    fields: dict = {}
    numbers: dict[str, int] = {}
    picked = dict(picked or {})
    more = picked.pop("more", []) or []
    for name, n in picked.items():
        if name not in FIELD_NAMES:
            errors.append(f"source {part['source_id']}: {name} is not one of "
                          f"{', '.join(FIELD_NAMES)} or more")
            continue
        if not isinstance(n, int) or n not in arr["fields"]:
            errors.append(f"source {part['source_id']}: field {n} for {name} is not in "
                          f"list {list_n} (pick from {arr['fields']})")
            continue
        f = found["fields"][n - 1]
        if not _field_role_ok(name, f["role"]):
            errors.append(f"source {part['source_id']}: field {n} is {f['role']}, not {name}")
            continue
        ref = f["path"]
        if f["role"] == "image_ref":
            tmpl = str(part.get("image_template") or "")
            if not tmpl:
                errors.append(f"source {part['source_id']}: field {n} is a picture name "
                              f"with no address to put it in; pick another")
                continue
            ref = "tmpl:" + tmpl.replace("{image}", "{" + f["path"] + "}")
        fields[name] = ref
        numbers[name] = n
    used = set(numbers.values())
    for n in more if isinstance(more, list) else []:
        if not isinstance(n, int) or n not in arr["fields"] or n in used:
            continue        # a wrong extra column is left out, not argued over
        if sum(k not in FIELD_NAMES for k in fields) >= MORE_MAX:
            break
        f = found["fields"][n - 1]
        if f["role"] == "image_ref":
            continue        # a picture's name is no column without its address
        key = more_key(f["path"], set(fields))
        fields[key] = f["path"]
        numbers[key] = n
        used.add(n)
    documented = part.get("meanings") or {}
    for n in arr["fields"]:
        # Every field the source documents a scale for is shown, picked or not.
        f = found["fields"][n - 1]
        if n in used or f["path"] not in documented or f["role"] == "image_ref" \
                or sum(k not in FIELD_NAMES for k in fields) >= MORE_MAX or not fields:
            continue
        key = more_key(f["path"], set(fields))
        fields[key] = f["path"]
        numbers[key] = n
        used.add(n)
    if not fields:
        errors.append(f"source {part['source_id']}: pick at least one field")
    src = {"id": part["source_id"], "kind": part.get("kind") or "http_json",
           "service": str(part.get("service") or ""), "door": str(part.get("door") or ""),
           "url": str(part.get("url") or ""), "items": arr["path"], "fields": fields}
    labels, meanings = {}, {}
    for key, n in numbers.items():
        said = words.get(n) or {}
        f = found["fields"][n - 1]
        label = plain_text(said.get("label"), LABEL_MAX) or f.get("label") \
            or shape.humanize(f["path"])
        if label:
            labels[key] = label
        means = plain_text((part.get("meanings") or {}).get(f["path"]), MEANS_MAX) \
            or plain_text(said.get("means"), MEANS_MAX)
        if means:
            meanings[key] = means
    if labels:
        src["labels"] = labels
    if meanings:
        src["meanings"] = meanings
    if part.get("name"):
        src["name"] = str(part["name"])[:80]
    if part.get("about"):
        src["about"] = _about(part)[:300]     # what it is: the proof checks it fits the job
    # When each row is (a start time in the answer plus an offset field) and
    # words for scale values: from the website row, by this source's names.
    tb = part.get("time_base") if isinstance(part.get("time_base"), dict) else {}
    key_of = {path: key for key, path in fields.items() if isinstance(path, str)}
    if tb.get("start") and key_of.get(str(tb.get("offset") or "")):
        src["time"] = {"start": str(tb["start"]), "format": str(tb.get("format") or ""),
                       "unit": str(tb.get("unit") or "hours"),
                       "field": key_of[str(tb["offset"])]}
    words_by = part.get("value_words") if isinstance(part.get("value_words"), dict) else {}
    vw = {key_of[p]: w for p, w in words_by.items() if p in key_of and isinstance(w, list)}
    if vw:
        src["value_words"] = vw
    if part.get("topic") and "{topic}" in src["url"]:
        src["topic"] = str(part["topic"])[:40]     # asked with no words: this subject
    holes = _PLACE_HOLES.findall(src["url"])
    if holes:
        src["args"] = {h: f"setting:home_town.{h.split('_')[0]}" for h in holes}
    return src, errors


def _labels_by_source(picks: dict) -> dict[str, dict[int, dict]]:
    out: dict[str, dict[int, dict]] = {}
    for row in picks.get("labels") or []:
        if isinstance(row, dict) and isinstance(row.get("field"), int):
            out.setdefault(str(row.get("source") or ""), {})[row["field"]] = row
    return out


def expect_for(view_types: list[str], fields: dict) -> dict:
    """What a check's answer must hold, from the views that show it: a gallery
    pictures, a map positions, a player something to play, a forecast times;
    any other view more than a name."""
    want: list[str] = []
    for v in view_types:
        want += {"gallery": ["image"], "map": ["lat", "lon"], "player": ["stream"],
                 "forecast": ["when"]}.get(v, [])
    if not want:
        want = [f for f in ("title", "value", "text") if f in fields][:1]
    want = list(dict.fromkeys(f for f in want if f in fields))
    return {"min_items": 1, "fields": want, "views": list(dict.fromkeys(view_types))}


def _with_words(view: dict, src: dict) -> dict:
    """A view carries its source's labels and value meanings for the page."""
    for key in ("labels", "meanings", "value_words"):
        if src.get(key):
            view[key] = dict(src[key])
    return view


def from_picks(app_id: str, caps: list[dict], parts: list[dict], picks: Any,
               title: str = "") -> tuple[dict, list[str]]:
    """The brain's picks as a spec. Returns ``(spec, errors)``: picks that do
    not fit the lists are errors in words the brain can act on."""
    if not isinstance(picks, dict):
        return {}, ["answer with one JSON object"]
    errors: list[str] = []
    by_id = {p["source_id"]: p for p in parts}
    sources: dict[str, dict] = {}
    views: list[dict] = []
    picked_fields = picks.get("fields") if isinstance(picks.get("fields"), dict) else {}
    words = _labels_by_source(picks)
    search = {s for s in picks.get("search") or [] if isinstance(s, str)}
    for i, v in enumerate(picks.get("views") or []):
        if not isinstance(v, dict):
            errors.append(f"views[{i}] must be an object")
            continue
        n, sid = v.get("view"), v.get("source")
        if not isinstance(n, int) or not 1 <= n <= len(VIEWS):
            errors.append(f"views[{i}]: view {n} is not on the list (1 to {len(VIEWS)})")
            continue
        if sid not in by_id:
            errors.append(f"views[{i}]: source {sid} is not one of {', '.join(by_id)}")
            continue
        if sid not in sources:
            src, errs = _source_of(by_id[sid], int(v.get("list") or 1), picked_fields.get(sid),
                                   words.get(sid))
            errors.extend(errs)
            if not src:
                continue
            sources[sid] = src
        view = {"type": VIEWS[n - 1], "source": sid}
        if view["type"] == "gallery":
            view["lightbox"] = True
        if sid in search and view["type"] in ("gallery", "table", "list", "links"):
            view["search"] = True
        if view["type"] == "player":
            stream = sources[sid]["fields"].get("stream")
            role = next((f["role"] for f in by_id[sid]["found"]["fields"]
                         if f["path"] == stream), "")
            view["mode"] = _mode_of(role)
            if not view["mode"]:
                errors.append(f"views[{i}]: a player needs a stream field (hls, youtube, "
                              f"audio or video)")
                continue
        views.append(_with_words(view, sources[sid]))
    actions = []
    for sid in sources:
        searchable = sid in search
        actions.append({"id": f"{'search' if searchable else 'get'}_{sid}"[:40], "source": sid,
                        "input": {"q": "string"} if searchable else {}})
    checks = []
    cap_ids = [c["id"] for c in caps]
    for i, c in enumerate(picks.get("checks") or []):
        if not isinstance(c, dict):
            continue
        n, sid = c.get("cap"), c.get("source")
        if not isinstance(n, int) or not 1 <= n <= len(caps):
            errors.append(f"checks[{i}]: capability {n} is not on the list (1 to {len(caps)})")
            continue
        if sid not in sources:
            errors.append(f"checks[{i}]: source {sid} has no view")
            continue
        action = next(a for a in actions if a["source"] == sid)
        q = " ".join(str(c.get("q") or "").split())[:60]
        if "{q}" not in sources[sid]["url"] and "{topic}" not in sources[sid]["url"]:
            q = ""      # the site cannot search: the check asks for its newest items
        checks.append({"cap": cap_ids[n - 1],
                       "ask": " ".join(str(c.get("ask") or "").split())[:200]
                       or ask_for(caps[n - 1]),
                       "action": action["id"],
                       "input": {"q": q} if action["input"] else {},
                       "expect": expect_for([v["type"] for v in views if v["source"] == sid],
                                            sources[sid]["fields"])})
    spec = _assemble(app_id, str(picks.get("title") or title), cap_ids, list(sources.values()),
                     views, actions, checks, caps=caps)
    return spec, errors


def _assemble(app_id: str, title: str, cap_ids: list[str], sources: list[dict],
              views: list[dict], actions: list[dict], checks: list[dict],
              caps: Optional[list[dict]] = None) -> dict:
    settings = []
    if any("args" in s for s in sources):
        settings.append({"key": "home_town", "kind": "place", "use": "home_location",
                         "label": "Your town"})
        views = views + [{"type": "settings", "source": ""}]
    title = re.sub("\\s*[\u2014\u2013]\\s*", ", ", " ".join(title.split()))[:60] or "Your app"
    out = {"schema": SPEC_SCHEMA, "id": app_id, "title": title, "caps": cap_ids,
           "settings": settings, "sources": sources, "views": views, "actions": actions,
           "checks": checks}
    words = {c["id"]: " ".join(f"{c.get('name') or ''}: {c.get('does') or ''}".split())
             .strip(": ")[:300] for c in caps or [] if c.get("id") in cap_ids}
    if words:
        out["cap_words"] = words
    return out


_WEATHER_RE = re.compile(r"(temp|cloud|rain|precip|wind|humid|snow|seeing|transparen|"
                         r"visib|pressure|\buv\b|dew|gust|storm|sky|weather|frost)", re.I)


def fallback_view(part: dict, fields: dict, caps: list[dict]) -> str:
    """The fixed spec's view for one part: what the capability asks for when
    the data has it, else what the data has (pictures, positions, something
    to play, a forecast), else a table."""
    has = {"gallery": "image" in fields, "map": "lat" in fields and "lon" in fields,
           "player": "stream" in fields,
           "forecast": "when" in fields and any(
               k not in FIELD_NAMES and _WEATHER_RE.search(k) for k in fields)}
    for cap in caps:
        for view in cap_views(cap):
            if has.get(view):
                return view
    for view in ("gallery", "player", "forecast", "map"):
        if has[view]:
            return view
    return "table"


def fallback(app_id: str, caps: list[dict], parts: list[dict], title: str = "") -> dict:
    """The fixed spec: each part's first item list, with its pictures, times,
    positions and up to ``MORE_MAX`` more of its own fields as columns; one
    view (see :func:`fallback_view`), one action per part, one check per
    capability."""
    sources, views, actions = [], [], []
    for part in parts:
        found = part["found"]
        arrays = found.get("arrays") or []
        if not arrays:
            continue
        arr = arrays[0]
        fields: dict = {}
        more: list[int] = []
        for n in arr["fields"]:
            f = found["fields"][n - 1]
            slot = None
            for name in FIELD_NAMES:
                if name in fields or name == "value" or not _field_role_ok(name, f["role"]):
                    continue
                if f["role"] == "other" and name in ("title", "text"):
                    continue    # a kind or a code is its own column, not the name
                if f["role"] == "image_ref" and not part.get("image_template"):
                    continue
                slot = name
                break
            if slot:
                fields[slot] = n
            elif f["role"] in ("number", "date", "other", "text", "title") and \
                    len(more) < MORE_MAX:
                more.append(n)
        src, _errs = _source_of(part, 1, {**fields, "more": more})
        if not src:
            continue
        sources.append(src)
        kind = fallback_view(part, src["fields"], caps)
        view = {"type": kind, "source": src["id"]}
        if kind == "gallery":
            view["lightbox"] = True
        if kind == "player":
            stream = src["fields"].get("stream")
            view["mode"] = _mode_of(next((f["role"] for f in found["fields"]
                                          if f["path"] == stream), "")) or "video"
        views.append(_with_words(view, src))
        actions.append({"id": f"get_{src['id']}"[:40], "source": src["id"], "input": {}})
    checks = []
    for i, cap in enumerate(caps):
        if not actions:
            break
        action = actions[min(i, len(actions) - 1)]
        src = next(s for s in sources if s["id"] == action["source"])
        checks.append({"cap": cap["id"], "ask": ask_for(cap),
                       "action": action["id"], "input": {},
                       "expect": expect_for([v["type"] for v in views
                                             if v["source"] == src["id"]], src["fields"])})
    return _assemble(app_id, title or (caps[0]["name"] if caps else ""),
                     [c["id"] for c in caps], sources, views, actions, checks, caps=caps)


def fit_problems(spec: dict, caps: list[dict],
                 meaning_fit: Optional[Callable[[str, str], Optional[float]]] = None
                 ) -> list[str]:
    """Does each check fit its capability? Its view must be what the
    capability's name asks for (pictures, a map...), and its ask must be about
    the capability (all-MiniLM, when the reading helper is here)."""
    from puffpai.assemble import accept
    fit = meaning_fit or accept.meaning_fit
    errors = []
    for i, check in enumerate(spec.get("checks") or []):
        cap = next((c for c in caps if c["id"] == check.get("cap")), None)
        if cap is None:
            continue
        n = caps.index(cap) + 1
        action = next((a for a in spec.get("actions") or [] if a["id"] == check["action"]), {})
        shown = {v["type"] for v in spec.get("views") or [] if v.get("source") ==
                 action.get("source")}
        for view in cap_views(cap):
            if view not in shown:
                errors.append(f"checks[{i}]: capability {n} needs {VIEW_NEEDS[view]}: show "
                              f"its source as view {VIEWS.index(view) + 1} ({view})")
        sim = fit(str(check.get("ask") or ""), accept.cap_text(cap))
        if sim is not None and sim < accept.ASK_FIT:
            errors.append(f"checks[{i}]: the ask is not about capability {n} "
                          f"({cap['name']}); ask about it")
    return errors


def _default_ask(messages: list[dict]) -> str:
    """One call to the build's own brain (tests pass their own *ask*)."""
    from puffpai.config import get_config
    from puffpai.inference.ollama_backend import OllamaBackend
    cfg = get_config()
    backend = OllamaBackend(url=cfg.ollama.url, model=cfg.ollama.model, num_ctx=8192,
                            num_predict=900, timeout=180, think=False)
    return str(backend.chat(messages, temperature=0.1, max_tokens=900,
                            format=PICKS_FORMAT) or "")


def _json_of(text: str) -> Any:
    raw = str(text or "").strip()
    m = re.search(r"\{.*\}", raw, re.S)
    try:
        return json.loads(m.group(0) if m else raw)
    except ValueError:
        return None


def build(app_id: str, caps: list[dict], parts: list[dict], *, title: str = "",
          ask: Optional[Callable[[list[dict]], str]] = None,
          meaning_fit: Optional[Callable[[str, str], Optional[float]]] = None) -> dict:
    """``{"spec", "how": "brain"|"fallback", "rounds", "errors"}``.

    *parts*: ``[{"source_id", "kind", "url", "door", "service", "found",
    "image_template"?, "name"?, "about"?}]`` where *found* is
    :func:`shape.candidates` and *about* is what the source says about itself
    (the brain writes labels and value meanings from it)."""
    usable = [p for p in parts if (p.get("found") or {}).get("arrays")]
    if not usable:
        return {"spec": {}, "how": "none", "rounds": 0,
                "errors": ["no part had a list of items to show"]}
    ask = ask or _default_ask
    messages = [{"role": "user", "content": prompt(caps, usable)}]
    errors: list[str] = []
    rounds = 0
    for rounds in range(1, REPAIR_ROUNDS + 2):
        try:
            text = ask(messages)
        except Exception as exc:  # noqa: BLE001 - no brain, use the fixed spec
            errors = [f"the brain did not answer ({type(exc).__name__})"]
            break
        spec, errors = from_picks(app_id, caps, usable, _json_of(text), title)
        if not errors:
            errors = validate(spec)
        if not errors:
            errors = fit_problems(spec, caps, meaning_fit)
        if not errors:
            return {"spec": spec, "how": "brain", "rounds": rounds, "errors": []}
        messages = messages + [
            {"role": "assistant", "content": str(text)[:4000]},
            {"role": "user", "content": "That does not fit the lists:\n- "
             + "\n- ".join(errors[:12]) + "\nAnswer again with the whole JSON."}]
    spec = fallback(app_id, caps, usable, title)
    return {"spec": spec, "how": "fallback", "rounds": rounds, "errors": errors,
            "fallback_problems": validate(spec)}


__all__ = ["FIELD_NAMES", "INPUT_TYPES", "PICKS_FORMAT", "PLAYER_MODES", "SCHEMA",
           "SETTING_KINDS", "SOURCE_KINDS", "VIEWS", "ask_for", "build", "cap_views",
           "expect_for", "fallback", "fit_problems", "from_picks", "plain_text",
           "prompt", "resolve_args", "resolve_place", "save_setting", "setting_values",
           "validate"]
