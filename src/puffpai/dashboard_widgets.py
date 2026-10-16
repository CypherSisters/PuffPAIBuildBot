"""
PuffPAI Dashboard Widget System — the modular, LLM-managed dashboard.

Generalizes the camera-tile pattern: ONE registry, ONE catalog, generic
agent tools. Adding a new widget type = a catalog entry + a frontend
renderer — no new endpoints, tools, or routing per use case.

Catalog entries declare a consent_class so gating can be enforced per type:
  - internal:        no gate (calendar, weather, ...)
  - device:          Drawbridge per-device consent (cameras — handled by
                     dashboard_registry, rendered by the Security Cameras
                     panel, exposed here as virtual read-only widgets)
  - external_domain: outbound traffic to the public internet. Every fetch goes
                     through a named door in puffpai.security.egress_doors,
                     which refuses it before any socket opens unless the build's
                     run posture is `enumerated` AND the owner opened that door
                     by name. Every decision — sent or refused — is logged
                     server-side to <state dir>/security_logs/widget_egress.jsonl.

Registry persists at <data dir>/data/dashboard_widgets.json. Neither file is
ever written into the install folder when the build keeps its state elsewhere
(``PUFFPAI_STATE_DIR`` / ``PUFFPAI_DATA_DIR``); an egress log an older build
left in the install folder is moved over the first time a line is written.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
#: Fixed paths for tests; ``None`` means "resolve from this build's own dirs
#: when used" (so a data or state dir set after import still counts).
_REGISTRY_PATH: str | None = None
_EGRESS_LOG: str | None = None
#: Where the egress log used to be written (the install folder), for the move.
_LEGACY_EGRESS_LOG = os.path.join(_REPO_ROOT, "security_logs", "widget_egress.jsonl")


def _registry_path() -> str:
    """``<data dir>/data/dashboard_widgets.json`` (honours PUFFPAI_DATA_DIR)."""
    if _REGISTRY_PATH:
        return _REGISTRY_PATH
    try:
        from puffpai.config import get_config
        base = get_config().DATA_DIR
    except Exception:  # pragma: no cover - config is always importable
        base = _REPO_ROOT
    return os.path.join(base, "data", "dashboard_widgets.json")


def _egress_log_path() -> str:
    """``<state dir>/security_logs/widget_egress.jsonl``.

    The state dir, like every other log BuildBot keeps about what left the
    computer. It used to be the install folder, which ignored the build's own
    state dir and put a log one ``git add`` away from being published.
    """
    if _EGRESS_LOG:
        return _EGRESS_LOG
    from puffpai.config import get_state_dir
    return os.path.join(get_state_dir(), "security_logs", "widget_egress.jsonl")


def _move_legacy_log(target: str) -> None:
    """Move a log an older build wrote into the install folder. Once; quiet."""
    legacy = _LEGACY_EGRESS_LOG
    try:
        if (os.path.abspath(legacy) == os.path.abspath(target)
                or not os.path.isfile(legacy)):
            return
        with open(legacy, "r", encoding="utf-8") as src:
            old = src.read()
        if old and not old.endswith("\n"):
            old += "\n"
        with open(target, "a", encoding="utf-8") as dst:
            dst.write(old)
        os.remove(legacy)
        try:
            os.rmdir(os.path.dirname(legacy))  # only when now empty
        except OSError:
            pass
    except OSError:
        pass

_lock = threading.Lock()

# ── Widget catalog ───────────────────────────────────────────────────────────

WIDGET_CATALOG: dict[str, dict] = {
    "news": {
        "title": "News Feed",
        "consent_class": "external_domain",
        "props": {"url": "(required unless source given)", "source": "shortcut name",
                  "title": "display title", "max_items": "default 6"},
        "description": "Headlines from an RSS/Atom feed, fetched server-side.",
    },
    "calendar": {
        "title": "Calendar",
        "consent_class": "internal",
        "props": {"title": "display title", "days_ahead": "default 7"},
        "description": "Upcoming events and tasks from the PuffPAI calendar.",
    },
    "alarmflow": {
        "title": "Timers & Alarms",
        "consent_class": "internal",
        "props": {"title": "display title"},
        "description": "Timers & Alarms: active countdowns at a glance. "
                       "Reads live from app_data/alarmflow.json.",
    },
    "camera": {
        "title": "Security Camera",
        "consent_class": "device",
        "props": {"entity_id": "HA camera entity"},
        "description": "Live camera tile. Add via dashboard_add_camera "
                       "(Drawbridge consent applies); shown in the Security "
                       "Cameras panel.",
    },
    "now-playing": {
        "title": "Now Playing",
        "consent_class": "internal",
        "props": {"title": "display title"},
        "description": "Jellyfin now-playing: album art, transport controls, "
                       "volume. LAN-only media server.",
    },
    # One installed app's live card (picture, number or list). The widget holds
    # no address of its own: it shows what the app's feed already fetched on
    # the server, through that app's own outside service, which stays shut
    # until the owner allows it. The browser only ever talks to this box.
    "app-feed": {
        "title": "Live card",
        "consent_class": "external_domain",
        "props": {"app_id": "an installed app that shows live data",
                  "title": "display title"},
        "description": "The live card of one installed app, from data this "
                       "box fetched and stored. Needs that app's outside "
                       "service allowed by the owner.",
    },
}

# Known feeds so the LLM never has to invent URLs.
FEED_DIRECTORY: dict[str, dict] = {
    "npr":          {"url": "https://feeds.npr.org/1001/rss.xml", "title": "NPR News"},
    "bbc":          {"url": "https://feeds.bbci.co.uk/news/rss.xml", "title": "BBC News"},
    "hacker news":  {"url": "https://news.ycombinator.com/rss", "title": "Hacker News"},
    "ars technica": {"url": "https://feeds.arstechnica.com/arstechnica/index", "title": "Ars Technica"},
    "the guardian": {"url": "https://www.theguardian.com/world/rss", "title": "The Guardian"},
    "the verge":    {"url": "https://www.theverge.com/rss/index.xml", "title": "The Verge"},
}


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ── Registry persistence ─────────────────────────────────────────────────────

def _load() -> list[dict]:
    try:
        with open(_registry_path(), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []


def _save(items: list[dict]) -> None:
    path = _registry_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(items, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


#: Widget types that read one of the first apps' data (puffpai.first_apps).
#: While that app is turned off the widget is neither added nor shown.
WIDGET_APP = {"calendar": "calendar", "alarmflow": "alarmflow"}


def _app_off(wtype: str) -> str:
    """The app's turned-off line when this widget type's app is off, else ""."""
    app_id = WIDGET_APP.get(wtype)
    if not app_id:
        return ""
    try:
        from puffpai import first_apps
        return first_apps.turned_off_line(app_id) if first_apps.is_off(app_id) else ""
    except Exception:  # noqa: BLE001 - never hide a widget over this check
        return ""


def list_widgets(include_cameras: bool = False) -> list[dict]:
    items = [w for w in _load() if not _app_off(str(w.get("type") or ""))]
    if include_cameras:
        try:
            from puffpai import dashboard_registry as _dreg
            for cam in _dreg.list_cameras():
                items.append({
                    "id": f"cam::{cam['entity_id']}",
                    "type": "camera",
                    "props": {"entity_id": cam["entity_id"], "title": cam.get("label")},
                    "added_by": cam.get("added_by"),
                    "added_at": cam.get("added_at"),
                    "virtual": True,  # managed by dashboard_registry
                })
        except Exception:
            pass
    return items


def add_widget(wtype: str, props: dict, added_by: str = "agent") -> tuple[bool, str, dict | None]:
    """Add a widget. Returns (ok, message, widget)."""
    wtype = (wtype or "").strip().lower()
    if wtype not in WIDGET_CATALOG:
        known = ", ".join(sorted(WIDGET_CATALOG))
        return False, f"Unknown widget type '{wtype}'. Known types: {known}.", None
    if wtype == "camera":
        return False, ("Cameras are Drawbridge-gated. Use dashboard_add_camera "
                       "(or POST /dashboard/cameras) instead."), None
    off = _app_off(wtype)
    if off:
        return False, off, None

    props = dict(props or {})
    if wtype == "news":
        source = (props.pop("source", "") or "").strip().lower()
        if source and not props.get("url"):
            hit = FEED_DIRECTORY.get(source)
            if not hit:
                # loose match
                hit = next((v for k, v in FEED_DIRECTORY.items()
                            if source in k or k in source), None)
            if not hit:
                known = ", ".join(sorted(FEED_DIRECTORY))
                return False, (f"I don't know a feed for '{source}'. Known "
                               f"sources: {known}. Or give me a feed URL."), None
            props.setdefault("title", hit["title"])
            props["url"] = hit["url"]
        if not props.get("url"):
            known = ", ".join(sorted(FEED_DIRECTORY))
            return False, (f"A news widget needs a source or URL. Known "
                           f"sources: {known}."), None
        props.setdefault("title", "News")
        props["max_items"] = int(props.get("max_items", 6) or 6)
    elif wtype == "calendar":
        props.setdefault("title", "Upcoming")
        props["days_ahead"] = int(props.get("days_ahead", props.pop("days", 7)) or 7)
    elif wtype == "now-playing":
        props.setdefault("title", "Now Playing")
    elif wtype == "alarmflow":
        props.setdefault("title", "Timers & Alarms")
    elif wtype == "app-feed":
        problem = _app_feed_props(props)
        if problem:
            return False, problem, None

    widget = {
        "id": "w_" + uuid.uuid4().hex[:8],
        "type": wtype,
        "props": props,
        "added_by": added_by,
        "added_at": _utcnow(),
    }
    with _lock:
        items = _load()
        # Dedupe: same type + same url/title
        for w in items:
            if w["type"] == wtype and (
                    (wtype == "news" and w["props"].get("url") == props.get("url"))
                    or (wtype == "app-feed"
                        and w["props"].get("app_id") == props.get("app_id"))
                    or (wtype in ("calendar", "now-playing", "alarmflow"))):
                return False, (f"A {wtype} widget"
                               f"{' for that feed' if wtype == 'news' else ''} "
                               f"is already on the dashboard "
                               f"('{w['props'].get('title')}')."), w
        items.append(widget)
        _save(items)
    return True, (f"Done, '{props.get('title', wtype)}' ({wtype} widget) is "
                  f"now on the dashboard."), widget


def _app_feed_props(props: dict) -> str:
    """Check an app-feed widget's props in place. '' when usable.

    Only an installed app with a valid live-data block may be named; the
    props are cut down to the app id and a bounded title, so nothing else an
    agent or a caller supplies (an address, a host) is ever stored.
    """
    from puffpai import app_registry, feeds
    app_id = str(props.get("app_id") or "").strip()
    if not app_registry.valid_id(app_id):
        return "Which app? Give the name of an installed app that shows live data."
    match = next((m for m, _f in feeds.feed_apps() if m.get("id") == app_id), None)
    if match is None:
        return (f"No installed app called '{app_id}' shows live data, so there "
                f"is nothing to put on the dashboard.")
    title = str(props.get("title") or match.get("name") or app_id)[:64]
    props.clear()
    props.update({"app_id": app_id, "title": title})
    return ""


def remove_widget(query: str) -> tuple[bool, str]:
    q = (query or "").strip().lower().strip("'\"")
    if not q:
        return False, "Which widget? Give me its title or type."
    with _lock:
        items = _load()
        for w in items:
            hay = f"{w['id']} {w['type']} {w['props'].get('title','')}".lower()
            if q == w["id"].lower() or q == w["type"] or all(
                    t in hay for t in q.split()):
                items.remove(w)
                _save(items)
                return True, (f"Removed '{w['props'].get('title', w['type'])}' "
                              f"from the dashboard.")
    return False, f"No dashboard widget matches '{query}'."


def configure_widget(widget_id: str, props: dict) -> tuple[bool, str]:
    with _lock:
        items = _load()
        for w in items:
            if w["id"] == widget_id:
                w["props"].update(props or {})
                _save(items)
                return True, f"Updated '{w['props'].get('title', w['type'])}'."
    return False, f"No widget with id {widget_id}."


def get_widget(widget_id: str) -> dict | None:
    return next((w for w in _load() if w["id"] == widget_id), None)


# ── Environment (forecast hi/lo + air quality; open-meteo, keyless) ─────────

_env_cache: dict[str, tuple[float, dict]] = {}
_ENV_TTL = 1800  # 30 min

# Neutral default coordinates (Greenwich); override via config location lat/lon.
# The weather widget uses these until the user sets their own location.
_DEFAULT_LAT, _DEFAULT_LON = 51.4779, -0.0015

_AQI_LABELS = [(50, "Good"), (100, "Moderate"), (150, "Unhealthy (sensitive)"),
               (200, "Unhealthy"), (300, "Very unhealthy"), (10 ** 6, "Hazardous")]


_RISK_LABELS = ["Low", "Moderate", "High", "Very High"]


def _risk(level: int, detail: str) -> dict:
    level = min(3, max(0, int(level)))
    return {"level": level, "label": _RISK_LABELS[level], "detail": detail}


def _num(v):
    return v if isinstance(v, (int, float)) else None


def _air_breakdown(p: dict, rh, precip) -> dict:
    """Health-focused air view for the weather card: mold, dust,
    pollen/allergens, and an asthma composite. Levels 0-3
    (Low/Moderate/High/Very High), each with a short 'why' string.

    Bands follow EPA AQI breakpoints where a pollutant is measured.
    Pollen: open-meteo's CAMS pollen coverage is Europe-only today, so when
    no station data comes back we fall back to the South-Central-Texas
    seasonal calendar and clearly label it a seasonal estimate.
    Informational only — not medical advice."""
    b: dict = {}

    # Mold — moisture-driven: relative humidity + precipitation
    lvl, drivers = 0, []
    rh = _num(rh)
    if rh is not None:
        drivers.append(f"humidity {round(rh)}%")
        if rh >= 85:
            lvl = 2
        elif rh >= 65:
            lvl = 1
    precip = _num(precip)
    if precip is not None and precip > 0:
        drivers.append("rain today")
        lvl = min(3, lvl + 1)
    b["mold"] = _risk(lvl, ", ".join(drivers) or "no humidity data")

    # Dust — PM10 (EPA bands) plus mineral/Saharan dust when reported
    pm10, dust = _num(p.get("pm10")), _num(p.get("dust"))
    lvl, detail = 0, "no data"
    if pm10 is not None:
        lvl = 0 if pm10 <= 54 else 1 if pm10 <= 154 else 2 if pm10 <= 254 else 3
        detail = f"PM10 {round(pm10)} ug/m3"
    if dust is not None and dust >= 20:
        lvl = min(3, lvl + 1)
        detail += f", airborne dust {round(dust)} ug/m3"
    b["dust"] = _risk(lvl, detail)

    # Pollen / allergens — measured if the API has it, else seasonal calendar
    pollen = {k[:-7]: _num(v) for k, v in p.items()
              if k.endswith("_pollen") and _num(v) is not None}
    if pollen:
        worst = max(pollen, key=lambda k: pollen[k])
        wv = pollen[worst]
        lvl = 0 if wv < 20 else 1 if wv < 80 else 2 if wv < 200 else 3
        b["pollen"] = _risk(lvl, f"{worst} {round(wv)} grains/m3")
    else:
        month = datetime.now().month
        if month in (12, 1, 2):
            lvl, note = 2, "mountain cedar season"
        elif month in (3, 4, 5):
            lvl, note = 2, "oak & spring tree season"
        elif month in (9, 10, 11):
            lvl, note = 1, "ragweed & fall weed season"
        else:
            lvl, note = 1, "grass pollen season"
        b["pollen"] = _risk(lvl, f"{note} (seasonal estimate)")

    # Asthma — composite: worst of AQI band / PM2.5 / ozone, nudged up when
    # dust or pollen is elevated (common asthma triggers)
    aqi, pm25, o3 = _num(p.get("us_aqi")), _num(p.get("pm2_5")), _num(p.get("ozone"))
    lvl, drivers = 0, []
    if aqi is not None:
        lvl = 0 if aqi <= 50 else 1 if aqi <= 100 else 2 if aqi <= 150 else 3
        drivers.append(f"AQI {round(aqi)}")
    if pm25 is not None and pm25 >= 35:
        lvl = max(lvl, 2)
        drivers.append(f"PM2.5 {round(pm25)}")
    if o3 is not None and o3 >= 120:
        lvl = max(lvl, 2)
        drivers.append(f"ozone {round(o3)} ug/m3")
    lvl = max(lvl, b["dust"]["level"] - 1, b["pollen"]["level"] - 1)
    b["asthma"] = _risk(lvl, ", ".join(drivers) or "air data unavailable")
    return b


_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
_AIR_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"


def fetch_environment(lat: float = _DEFAULT_LAT, lon: float = _DEFAULT_LON,
                      unit: str = "fahrenheit") -> dict:
    """Daily high/low, US AQI, pollutant detail, and a health-focused air
    breakdown (mold / dust / pollen / asthma) from open-meteo (no API key).

    **Gated.** Both calls go through named egress doors — ``weather`` for the
    forecast host and ``air_quality`` for the air-quality host. Each is shut
    unless this build's run posture is ``enumerated`` and the owner opened that
    door by name, and a shut door means no socket is opened at all: the door is
    resolved before ``requests`` is even imported. This is what makes the
    README's deny-by-default claim true rather than aspirational.

    The returned dict always carries a ``doors`` block saying, per door,
    whether it is open and why — so the card can render the reason instead of
    polling a route that will never have anything for it. Cached 30 min once
    the forecast itself came back, keyed by which switches are on right now,
    so flipping a switch takes effect at once rather than after the TTL. (It
    used to skip the cache whenever anything was refused, so with air quality
    off every card and top-bar load asked the forecast again.)
    """
    from puffpai.security import egress_doors as _doors

    unit = "celsius" if unit == "celsius" else "fahrenheit"
    switches = tuple(bool(_doors.egress_allowed(d)[0])
                     for d in ("weather", "air_quality"))
    key = f"{lat:.3f},{lon:.3f},{unit},{switches}"
    now = time.time()
    cached = _env_cache.get(key)
    if cached and now - cached[0] < _ENV_TTL:
        return cached[1]
    out: dict = {}
    door_state: dict = {}
    rh = precip = None
    try:
        f = _doors.egress_get(
            "weather", _FORECAST_URL,
            params={"latitude": lat, "longitude": lon,  # type: ignore[arg-type]
                    "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum",
                    # The current reading too, in the same one request, so
                    # a build with no home hub still has a temperature now.
                    "current": ("relative_humidity_2m,temperature_2m,"
                                "apparent_temperature,weather_code"),
                    "temperature_unit": unit, "timezone": "auto",
                    "forecast_days": 1}).json()
        out["high"] = round(f["daily"]["temperature_2m_max"][0])
        out["low"] = round(f["daily"]["temperature_2m_min"][0])
        try:
            precip = (f["daily"].get("precipitation_sum") or [None])[0]
            rh = f.get("current", {}).get("relative_humidity_2m")
            cur = f.get("current") or {}
            out["now"] = {"temp": _num(cur.get("temperature_2m")),
                          "feels_like": _num(cur.get("apparent_temperature")),
                          "humidity": _num(rh),
                          "code": _num(cur.get("weather_code"))}
        except Exception:
            pass
        door_state["weather"] = {"open": True, "reason": ""}
        _log_egress(_FORECAST_URL, True, "daily hi/lo", door="weather",
                    decision="open")
    except _doors.EgressBlocked as blocked:
        door_state["weather"] = {"open": False, "reason": blocked.reason,
                                 "message": blocked.user_message}
        _log_egress(_FORECAST_URL, False, blocked.user_message, door="weather",
                    decision=blocked.reason, sent=False)
    except Exception as exc:
        # The door was open; the network or the service failed. Distinct from a
        # refusal, and reported as such.
        door_state["weather"] = {"open": True, "reason": ""}
        _log_egress(_FORECAST_URL, False, type(exc).__name__, door="weather",
                    decision="open")
    pollutants: dict = {}
    try:
        a = _doors.egress_get(
            "air_quality", _AIR_URL,
            params={"latitude": lat, "longitude": lon,  # type: ignore[arg-type]
                    "current": ("us_aqi,pm2_5,pm10,ozone,nitrogen_dioxide,dust,"
                                "alder_pollen,birch_pollen,grass_pollen,"
                                "mugwort_pollen,olive_pollen,ragweed_pollen")}).json()
        pollutants = a.get("current", {}) or {}
        aqi = pollutants.get("us_aqi")
        out["us_aqi"] = aqi
        out["pm2_5"] = pollutants.get("pm2_5")
        if isinstance(aqi, (int, float)):
            out["aqi_label"] = next(lbl for cap, lbl in _AQI_LABELS if aqi <= cap)
        door_state["air_quality"] = {"open": True, "reason": ""}
        _log_egress(_AIR_URL, True, f"us_aqi={aqi}", door="air_quality",
                    decision="open")
    except _doors.EgressBlocked as blocked:
        door_state["air_quality"] = {"open": False, "reason": blocked.reason,
                                     "message": blocked.user_message}
        _log_egress(_AIR_URL, False, blocked.user_message, door="air_quality",
                    decision=blocked.reason, sent=False)
    except Exception as exc:
        door_state["air_quality"] = {"open": True, "reason": ""}
        _log_egress(_AIR_URL, False, type(exc).__name__, door="air_quality",
                    decision="open")
    # Only when the air-quality door was actually open. With it shut there is
    # no measurement, and _air_breakdown's seasonal-calendar fallback would
    # render a pollen level and an asthma composite that came from a month
    # number — plausible-looking air data on a box that opened no socket. A
    # refused door says so; it does not quietly substitute an estimate.
    if door_state.get("air_quality", {}).get("open"):
        out["air_breakdown"] = _air_breakdown(pollutants, rh, precip)
    out["doors"] = door_state
    if "high" in out:
        _env_cache[key] = (now, out)
    return out


# ── News fetching (server-side; all widget egress goes through here) ─────────

_news_cache: dict[str, tuple[float, list[dict]]] = {}
_NEWS_TTL = 300  # seconds


def _log_egress(url: str, ok: bool, detail: str = "", door: str = "",
                decision: str = "", sent: bool = True) -> None:
    """Record one egress decision server-side.

    ``enforced`` is True because the decision is now made by
    :mod:`puffpai.security.egress_doors` *before* a socket opens, not observed
    after the fact. ``sent`` says whether anything actually left the box:
    ``sent: false`` is a refusal, and a refusal is the single most interesting
    line an owner can find in this file.

    The query string is dropped: an open-meteo URL carries the household's
    coordinates, and a security log is not the place to write them down.
    """
    try:
        path = _egress_log_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if not _EGRESS_LOG:
            _move_legacy_log(path)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": _utcnow(), "kind": "widget_egress",
                                "door": door, "url": url.split("?", 1)[0],
                                "ok": ok, "sent": sent,
                                "decision": decision[:200],
                                "detail": detail[:200],
                                "enforced": True}) + "\n")
    except OSError:
        pass


def _parse_feed(content: bytes) -> list[dict]:
    """Minimal RSS 2.0 / Atom parser — no external deps."""
    root = ET.fromstring(content)

    def tag(el):  # strip namespace
        return el.tag.split("}")[-1].lower()

    items: list[dict] = []
    for el in root.iter():
        if tag(el) not in ("item", "entry"):
            continue
        title, link, published = "", "", ""
        for child in el:
            t = tag(child)
            if t == "title":
                title = (child.text or "").strip()
            elif t == "link":
                link = (child.get("href") or child.text or "").strip()
            elif t in ("pubdate", "published", "updated") and not published:
                published = (child.text or "").strip()
        if title:
            items.append({"title": title, "link": link, "published": published})
    return items


def fetch_news(url: str, max_items: int = 6) -> tuple[bool, list[dict] | str]:
    """Fetch + parse a feed with caching. Returns (ok, items|error).

    Gated by the ``news_rss`` egress door. A shut door returns the door's own
    sentence rather than a generic failure, and — unlike a transport error —
    does not fall back to a stale cache: a build whose posture forbids reaching
    the internet should not keep serving what it fetched under a different one.
    """
    from puffpai.security import egress_doors as _doors

    now = time.time()
    cached = _news_cache.get(url)
    if cached and now - cached[0] < _NEWS_TTL:
        return True, cached[1][:max_items]
    try:
        resp = _doors.egress_get(
            "news_rss", url,
            headers={"User-Agent": "PuffPAI/1.0 (local dashboard widget)"})
        resp.raise_for_status()
        items = _parse_feed(resp.content)
    except _doors.EgressBlocked as blocked:
        _news_cache.pop(url, None)
        _log_egress(url, False, blocked.user_message, door="news_rss",
                    decision=blocked.reason, sent=False)
        return False, blocked.user_message
    except Exception as exc:
        _log_egress(url, False, type(exc).__name__, door="news_rss",
                    decision="open")
        if cached:  # stale-but-usable
            return True, cached[1][:max_items]
        return False, f"Could not fetch the feed: {exc}"
    _log_egress(url, True, f"{len(items)} items", door="news_rss",
                decision="open")
    _news_cache[url] = (now, items)
    return True, items[:max_items]


# ── The home page and sidebar layout (one plain JSON file) ───────────────────
#
# ``<state dir>/dashboard_layout.json`` says how the owner arranged her home
# page and her sidebar, so it is the same on every device and the brain can
# change it when asked in chat (``arrange_dashboard`` in home_things.py).
# The browser keeps a copy only as an offline cache. Shape (every key
# optional; a missing file means the defaults below):
#
#   {"version": 1,
#    "tiles":   {"order": [tile id, ...], "sizes": {tile id: {"w": 1-12, "h": 1-12}},
#                "hidden": [tile id, ...], "pinned": [tile id, ...],
#                "names": {tile id: "her name for it"}},
#    "sidebar": {"order": [href, ...], "hidden": [href, ...],
#                "pinned": [href, ...], "names": {href: "her name for it"}},
#    "seen":    {"tiles": {tile id: name}, "sidebar": {href: label}},
#    "updated_at": "...", "updated_by": "owner" | "brain"}
#
# Tile ids: core tiles (CORE_TILES), the tiles that start hidden
# (STARTS_HIDDEN), ``<kind>:<id>`` for each installed thing (GET /installed),
# and registry widget ids (``w_...``). ``seen`` is what the page last showed,
# written by the page, so the brain can find a tile or a menu item by name.

_LAYOUT_PATH: str | None = None
LAYOUT_VERSION = 1

#: Always offered on the home page (owner names).
CORE_TILES: dict[str, str] = {"chat": "Chat", "jobs": "Build jobs",
                              "find-more": "Find more"}
#: Tiles a fresh layout keeps in the hidden tray. The owner can bring any back.
STARTS_HIDDEN: dict[str, str] = {
    "weather": "Weather", "jetson": "This computer", "smart-home": "Smart home",
    "cameras": "Cameras", "schedule": "Schedule", "reminders": "Reminders",
    "meals": "Meals", "workout": "Workout", "front": "About your bot",
    "persona": "Your bot's own card",
}
LAYOUT_ACTIONS = ("pin", "unpin", "hide", "show", "move", "rename")
_SECTIONS = ("tiles", "sidebar")
_PLACES = {"home": "tiles", "tile": "tiles", "tiles": "tiles",
           "sidebar": "sidebar", "menu": "sidebar"}
_MAX_IDS = 200
_MAX_ID = 160
_MAX_NAME = 40
_SKIP_WORDS = frozenset("the my a an tile card page menu item link sidebar home "
                        "on of".split())


def _layout_path() -> str:
    if _LAYOUT_PATH:
        return _LAYOUT_PATH
    from puffpai.config import get_state_dir
    return os.path.join(get_state_dir(), "dashboard_layout.json")


def default_layout() -> dict:
    return {"version": LAYOUT_VERSION,
            "tiles": {"order": list(CORE_TILES), "sizes": {},
                      "hidden": list(STARTS_HIDDEN), "pinned": [], "names": {}},
            "sidebar": {"order": [], "hidden": [], "pinned": [], "names": {}},
            "seen": {"tiles": {}, "sidebar": {}}}


def _ids(value) -> list[str]:
    out: list[str] = []
    for v in value if isinstance(value, list) else []:
        s = str(v).strip()[:_MAX_ID] if isinstance(v, (str, int)) else ""
        if s and s not in out:
            out.append(s)
    return out[:_MAX_IDS]


def _names(value, size: int = _MAX_NAME) -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in (value.items() if isinstance(value, dict) else []):
        k, v = str(k).strip()[:_MAX_ID], " ".join(str(v or "").split())[:size]
        if k and v:
            out[k] = v
        if len(out) >= _MAX_IDS:
            break
    return out


def _sizes(value) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for k, v in (value.items() if isinstance(value, dict) else []):
        try:
            w, h = int(v.get("w")), int(v.get("h"))
        except (AttributeError, TypeError, ValueError):
            continue
        out[str(k)[:_MAX_ID]] = {"w": min(12, max(1, w)), "h": min(12, max(1, h))}
        if len(out) >= _MAX_IDS:
            break
    return out


def _section(value, base: dict) -> dict:
    value = value if isinstance(value, dict) else {}
    out: dict = {k: _ids(value.get(k, base.get(k)))
                 for k in ("order", "hidden", "pinned")}
    out["names"] = _names(value.get("names", base.get("names")))
    if "sizes" in base:
        out["sizes"] = _sizes(value.get("sizes", base.get("sizes")))
    return out


def _normal(data) -> dict:
    data = data if isinstance(data, dict) else {}
    base = default_layout()
    seen = data.get("seen") if isinstance(data.get("seen"), dict) else {}
    out: dict = {"version": LAYOUT_VERSION,
                 "tiles": _section(data.get("tiles"), base["tiles"]),
                 "sidebar": _section(data.get("sidebar"), base["sidebar"]),
                 "seen": {"tiles": _names(seen.get("tiles"), 80),
                          "sidebar": _names(seen.get("sidebar"), 80)}}
    for k in ("updated_at", "updated_by"):
        if isinstance(data.get(k), str):
            out[k] = data[k][:40]
    return out


def get_layout() -> dict:
    """The saved layout, or the defaults when there is none (or it is broken)."""
    try:
        with open(_layout_path(), encoding="utf-8") as f:
            return _normal(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError, OSError, UnicodeDecodeError):
        return _normal({})


def _write_layout(layout: dict, by: str) -> dict:
    layout = _normal(layout)
    layout["updated_at"] = _utcnow()
    layout["updated_by"] = "brain" if by == "brain" else "owner"
    path = _layout_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(layout, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)
    return layout


def save_layout(patch: dict, by: str = "owner") -> dict:
    """``PUT /dashboard/layout``: each top-level section given (``tiles``,
    ``sidebar``, ``seen``) replaces the saved one; the rest stays."""
    patch = patch if isinstance(patch, dict) else {}
    with _lock:
        layout = get_layout()
        for key in (*_SECTIONS, "seen"):
            if isinstance(patch.get(key), dict):
                layout[key] = patch[key]
        return _write_layout(layout, by)


def _norm_words(text) -> str:
    import re
    return " ".join(w for w in re.findall(r"[a-z0-9]+", str(text or "").lower())
                    if w not in _SKIP_WORDS)


def _candidates(layout: dict, place: str) -> dict[str, str]:
    """``{id: name}`` the owner would recognize in *place* (her names win)."""
    if place == "tiles":
        known = {**CORE_TILES, **STARTS_HIDDEN, **layout["seen"]["tiles"]}
    else:
        known = dict(layout["seen"]["sidebar"])
    known.update({k: v for k, v in layout[place]["names"].items() if k in known})
    return known


def find_layout_item(layout: dict, ref, place: str = "") -> tuple[str, str, str]:
    """``(place, id, name)`` for what the owner called *ref*, or ``("", "", "")``.
    *place* is ``tiles`` or ``sidebar``; empty looks at the home page first."""
    want = _norm_words(ref)
    raw = str(ref or "").strip()
    if not want and not raw:
        return "", "", ""
    for p in [place] if place in _SECTIONS else list(_SECTIONS):
        known = _candidates(layout, p)
        if raw in known:
            return p, raw, known[raw]
        # Her own name for it counts as well as the one the page shows.
        rows = [(i, n, {_norm_words(n), _norm_words(i),
                        _norm_words({**CORE_TILES, **STARTS_HIDDEN,
                                     **layout["seen"][p]}.get(i, ""))})
                for i, n in known.items()]
        tests = (lambda names: want in names,
                 lambda names: bool(want) and any(set(want.split()) <= set(x.split())
                                                  for x in names if x),
                 lambda names: any(x and set(x.split()) <= set(want.split())
                                   for x in names))
        for test in tests:
            hits = [(i, n) for i, n, names in rows if test(names)]
            if hits:
                i, n = min(hits, key=lambda h: len(h[1]))
                return p, i, n
    return "", "", ""


def _full_order(layout: dict, place: str) -> list[str]:
    order = list(layout[place]["order"])
    base = list(CORE_TILES) if place == "tiles" else []
    for i in base + list(layout["seen"][place]):
        if i not in order:
            order.append(i)
    return order


def arrange(action: str, ref, place: str = "", position=None,
            name: str = "", by: str = "brain") -> dict:
    """One change to the home page or the sidebar, saved to the layout file.

    *action*: pin, unpin, hide, show, move (*position*: ``first``, ``last``
    or a number from 1), rename (*name*; empty gives the own name back).
    Returns ``{ok, line, place, id, name}``."""
    action = str(action or "").strip().lower()
    place = _PLACES.get(str(place or "").strip().lower(), "")
    if action not in LAYOUT_ACTIONS:
        return {"ok": False, "place": "", "id": "", "name": "",
                "line": "I can pin, unpin, hide, show, move or rename a tile or a "
                        "menu item."}
    with _lock:
        layout = get_layout()
        where, tid, label = find_layout_item(layout, ref, place)
        if not tid:
            names = sorted(set(_candidates(layout, "tiles").values()))[:20]
            return {"ok": False, "place": "", "id": "", "name": str(ref or ""),
                    "line": (f"I could not find \"{ref}\" on your home page or in "
                             f"the menu. On the home page: {', '.join(names)}.")}
        sec = layout[where]
        spot = "home page" if where == "tiles" else "menu"
        new_name = " ".join(str(name or "").split())[:_MAX_NAME]
        if action == "hide":
            sec["hidden"] = [*[h for h in sec["hidden"] if h != tid], tid]
            sec["pinned"] = [p for p in sec["pinned"] if p != tid]
            line = f"{label} is hidden from your {spot}. Say so and I will bring it back."
        elif action == "show":
            sec["hidden"] = [h for h in sec["hidden"] if h != tid]
            line = f"{label} is back on your {spot}."
        elif action in ("pin", "unpin"):
            sec["pinned"] = [p for p in sec["pinned"] if p != tid]
            if action == "pin":
                sec["pinned"].insert(0, tid)
                sec["hidden"] = [h for h in sec["hidden"] if h != tid]
                line = f"{label} is pinned to the top of your {spot}."
            else:
                line = f"{label} is not pinned any more."
        elif action == "move":
            order = [i for i in _full_order(layout, where) if i != tid]
            pos = str(position if position is not None else "first").strip().lower()
            if pos in ("first", "top", "start", "0", "1"):
                at = 0
            elif pos in ("last", "bottom", "end"):
                at = len(order)
            else:
                try:
                    at = min(len(order), max(0, int(float(pos)) - 1))
                except ValueError:
                    at = 0
            order.insert(at, tid)
            sec["order"] = order
            sec["hidden"] = [h for h in sec["hidden"] if h != tid]
            # Pinned things lead; "first" means before them too.
            sec["pinned"] = [p for p in sec["pinned"] if p != tid]
            if at == 0 and sec["pinned"]:
                sec["pinned"].insert(0, tid)
            line = (f"{label} is first on your {spot} now." if at == 0
                    else f"{label} is last on your {spot} now." if at == len(order) - 1
                    else f"{label} is number {at + 1} on your {spot} now.")
        else:  # rename
            if new_name:
                sec["names"][tid] = new_name
                line = f"{label} is called {new_name} on your {spot} now."
            else:
                sec["names"].pop(tid, None)
                line = f"{label} has its own name back."
        _write_layout(layout, by)
    return {"ok": True, "line": line, "place": where, "id": tid,
            "name": new_name or label}
