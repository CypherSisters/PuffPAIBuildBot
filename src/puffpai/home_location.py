"""Where home is, roughly: the one place the bot learns its town, date and time.

Routines, local weather and anything else about "today, here" need three
things nobody had given the bot: a place, a date and a local clock. Before this
module the weather card silently used Greenwich and nothing else knew where
home was at all.

What is stored, and why
-----------------------
The ``home`` block in ``config/puffpai_config.json``::

    "home": {"label": "Boulder, Colorado, United States",
             "latitude": 40.0, "longitude": -105.3,
             "timezone": "America/Denver", "source": "town lookup",
             "set_at": "2026-09-23T16:00:00+00:00"}

* **Rounded coordinates only.** One decimal place is about 11 km: enough for
  local weather or daylight, not enough to find a house. The rounding happens
  before anything is written, and again on every read, so a hand-edited
  precise value is never used either.
* **In config, not the vault.** A routine that runs at 5 pm with nobody home
  cannot unlock the vault, so what it needs has to be readable without it.
  That is also why only the rough position is kept.
* **No default.** Unset means unset. Nothing here falls back to a made-up
  place; callers ask the owner instead.

Looking up a typed town name goes through the ``geocode`` outside service
(``geocoding-api.open-meteo.com``), which sends only the typed name. Typing the
position by hand sends nothing anywhere.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import math
import re
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Any, Optional

from puffpai.logging_config import get_logger

logger = get_logger(__name__)

CONFIG_KEY = "home"

#: Decimal places kept. One is about 11 km of latitude.
DECIMALS = 1

GEOCODE_DOOR = "geocode"
GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"

#: Said to the owner whenever a home is saved. Plain words, no jargon.
WHAT_IS_STORED = (
    "I keep only the town name, a rough map position (to about 11 km, so not "
    "your street or house), and your time zone. It is saved in this bot's "
    "settings on this computer, not locked in the vault, so daily updates can "
    "run while you are away.")


class HomeError(ValueError):
    """A home that cannot be set. ``str(exc)`` is a plain sentence for a person."""


def round_coord(value: float) -> float:
    """Round one coordinate to the stored precision."""
    return round(float(value), DECIMALS)


def _valid_number(value: Any, lo: float, hi: float) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number) or not lo <= number <= hi:
        return None
    return number


def _raw_block() -> Any:
    """The ``home`` block, read from the config file itself.

    Read from disk rather than the cached config object: ``puffpai home set``
    runs in a terminal while the backend keeps running, and the backend's
    routines must see the new home without a restart. One small file read.
    """
    try:
        import json
        import os
        from puffpai.config import get_config
        path = os.path.join(get_config().CONFIG_DIR, "puffpai_config.json")
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        return raw.get(CONFIG_KEY) if isinstance(raw, dict) else None
    except FileNotFoundError:
        return None
    except Exception as exc:  # never break a caller over a missing config
        logger.debug("home block unreadable: %s", exc)
        return None


def get_home() -> Optional[dict]:
    """The saved home, rounded and checked, or None when it is not set.

    Returns ``{"label", "latitude", "longitude", "timezone", "source"}``.
    A half-written or junk block counts as not set.
    """
    block = _raw_block()
    if not isinstance(block, dict):
        return None
    lat = _valid_number(block.get("latitude"), -90.0, 90.0)
    lon = _valid_number(block.get("longitude"), -180.0, 180.0)
    if lat is None or lon is None:
        return None
    label = str(block.get("label") or "").strip()[:120]
    tz_name = str(block.get("timezone") or "").strip()[:64]
    return {
        "label": label or f"{round_coord(lat)}, {round_coord(lon)}",
        "latitude": round_coord(lat),
        "longitude": round_coord(lon),
        "timezone": tz_name,
        "source": str(block.get("source") or "").strip()[:40],
    }


def is_set() -> bool:
    return get_home() is not None


def tz_for(home: Optional[dict]) -> tuple[tzinfo, str]:
    """The time zone to use for *home*, and a plain note on where it came from.

    An IANA name that this computer can read wins. Otherwise the computer's own
    clock is used, and the note says so rather than pretending.
    """
    name = (home or {}).get("timezone") or ""
    if name:
        try:
            from zoneinfo import ZoneInfo
            return ZoneInfo(name), name
        except Exception as exc:
            logger.debug("time zone %r unreadable here: %s", name, exc)
    local = datetime.now().astimezone().tzinfo or timezone.utc
    return local, "this computer's clock"


def local_now(home: Optional[dict] = None, now: Optional[datetime] = None) -> datetime:
    """Aware local time at home (or on this computer when home is unset)."""
    home = home if home is not None else get_home()
    tz, _ = tz_for(home)
    stamp = now or datetime.now(timezone.utc)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(tz)


def spoken_date(when: datetime) -> str:
    """'Wednesday, September 23'."""
    return f"{when.strftime('%A')}, {when.strftime('%B')} {when.day}"


def spoken_time(when: datetime) -> str:
    """'9 pm', '9:30 pm'."""
    h12 = when.hour % 12 or 12
    half = "am" if when.hour < 12 else "pm"
    return f"{h12} {half}" if when.minute == 0 else f"{h12}:{when.minute:02d} {half}"


def context_line(now: Optional[datetime] = None) -> str:
    """One plain line for the model's instructions: date, time and home town.

    For the build context. Never raises, never invents a place.
    """
    try:
        home = get_home()
        here = local_now(home, now)
        when = (f"Today is {spoken_date(here)}, {here.year}. "
                f"The local time is {spoken_time(here)}.")
        if home is None:
            return (when + " The owner has not told me their home town yet, "
                    "so I do not know where they are.")
        return when + f" Home is {home['label']} (a rough position only)."
    except Exception as exc:
        logger.debug("context line unavailable: %s", exc)
        return ""


# ── saving ────────────────────────────────────────────────────────────────

def _write(block: dict) -> dict:
    from puffpai import setup_ops
    setup_ops.write_main_config({CONFIG_KEY: block})
    home = get_home()
    if home is None:  # pragma: no cover - the writer reloads config
        raise HomeError("I could not save your home town. Please try again.")
    return home


_TZ_NAME_RE = re.compile(r"^(UTC|[A-Z][A-Za-z_]+(/[A-Za-z0-9_+-]+){1,2})$")


def _no_tz_list() -> bool:
    try:
        import zoneinfo
        return not zoneinfo.available_timezones()
    except Exception:  # noqa: BLE001
        return True


def set_home_coords(latitude: Any, longitude: Any, *, label: str = "",
                    timezone_name: str = "", source: str = "typed position") -> dict:
    """Save a typed position. Nothing leaves your computer without your consent."""
    lat = _valid_number(latitude, -90.0, 90.0)
    lon = _valid_number(longitude, -180.0, 180.0)
    if lat is None or lon is None:
        raise HomeError("That position does not look right. Latitude goes from "
                        "-90 to 90 and longitude from -180 to 180.")
    tz_name = (timezone_name or "").strip()
    if tz_name:
        try:
            from zoneinfo import ZoneInfo
            ZoneInfo(tz_name)
        except Exception:
            # A computer without the time zone list (Windows with no tzdata)
            # cannot check the name; a well-formed one is kept as it came
            # (tz_for falls back to this computer's clock when it can't read it).
            if not (_no_tz_list() and _TZ_NAME_RE.match(tz_name)):
                raise HomeError(f"I don't know the time zone {tz_name!r}. Use a "
                                "name like America/Denver or Europe/London.")
    block = {
        "label": (label or "").strip()[:120]
                 or f"{round_coord(lat)}, {round_coord(lon)}",
        "latitude": round_coord(lat),
        "longitude": round_coord(lon),
        "timezone": tz_name,
        "source": source,
        "set_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "skipped_in_setup": False,
    }
    return _write(block)


def note_skipped() -> None:
    """The owner left the town empty in the wizard. Nothing is looked up.

    Recorded (and nothing else) so the weather card can say plainly that the
    town was skipped and how to add it now.
    """
    from puffpai import setup_ops
    setup_ops.write_main_config({CONFIG_KEY: {"skipped_in_setup": True}})


def skipped_in_setup() -> bool:
    """Did the owner skip the town in the wizard, and it is still not set?"""
    block = _raw_block()
    return (isinstance(block, dict) and block.get("skipped_in_setup") is True
            and get_home() is None)


def clear_home() -> None:
    """Forget home. Writes an empty block so nothing falls back to a default."""
    from puffpai import setup_ops
    base = setup_ops._base_config()
    base[CONFIG_KEY] = {}
    setup_ops._write_json(setup_ops._config_path(), base)
    from puffpai.config import get_config
    get_config(_force_reload=True)


def _label_of(row: dict) -> str:
    parts = [str(row.get(k) or "").strip() for k in ("name", "admin1", "country")]
    seen: list[str] = []
    for part in parts:
        if part and part not in seen:
            seen.append(part)
    return ", ".join(seen)


def lookup_town(text: str, *, getter=None) -> list[dict]:
    """Ask the town-lookup service for places called *text*. Best match first.

    Sends only the name (the part before the first comma). Anything after a
    comma ("Springfield, Missouri") narrows the answers here, on this computer.
    Raises :class:`HomeError` in plain words when the service is not allowed or
    does not answer, and ``egress_doors.EgressBlocked`` is never let through
    raw.
    """
    from puffpai.security import egress_doors

    query = " ".join(str(text or "").split())[:100]
    if len(query) < 2:
        raise HomeError("Please type a town name.")
    name, _, rest = query.partition(",")
    hints = [h.strip().lower() for h in rest.split(",") if h.strip()]
    fetch = getter or egress_doors.egress_get
    try:
        resp = fetch(GEOCODE_DOOR, GEOCODE_URL,
                     params={"name": name.strip(), "count": 10,
                             "language": "en", "format": "json"},
                     headers={"User-Agent": egress_doors.FEED_USER_AGENT})
        data = resp.json()
    except egress_doors.EgressBlocked:
        raise HomeError("This bot is not allowed to look up towns online.")
    except Exception as exc:
        logger.info("town lookup failed: %s", type(exc).__name__)
        raise HomeError("The town lookup did not answer. Please try again, or "
                        "type the position instead.")
    rows = data.get("results") if isinstance(data, dict) else None
    out: list[dict] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        lat = _valid_number(row.get("latitude"), -90, 90)
        lon = _valid_number(row.get("longitude"), -180, 180)
        if lat is None or lon is None:
            continue
        where = " ".join(str(row.get(k) or "") for k in
                         ("admin1", "admin2", "country", "country_code")).lower()
        if hints and not all(h in where for h in hints):
            continue
        out.append({"label": _label_of(row), "latitude": lat, "longitude": lon,
                    "timezone": str(row.get("timezone") or "")})
    return out


def set_home_town(text: str, *, getter=None) -> tuple[dict, list[str]]:
    """Look up *text* and save the best match. Returns (home, other matches)."""
    found = lookup_town(text, getter=getter)
    if not found:
        raise HomeError(f"I couldn't find a town called {text.strip()!r}. Try "
                        "adding the state or country, like \"Springfield, "
                        "Missouri\".")
    best = found[0]
    home = set_home_coords(best["latitude"], best["longitude"],
                           label=best["label"], timezone_name=best["timezone"],
                           source="town lookup")
    others = [f["label"] for f in found[1:4] if f["label"] != best["label"]]
    return home, others


def geocode_allowed() -> tuple[bool, str]:
    """Can towns be looked up online right now? (allowed, plain reason)."""
    from puffpai.security import egress_doors
    ok, _why = egress_doors.egress_allowed(GEOCODE_DOOR)
    if ok:
        return True, ""
    posture = egress_doors.run_posture().get("run_egress")
    if posture != egress_doors.OPEN_POSTURE:
        return False, ("This bot is set up to keep everything on your "
                       "computer, so it can't look towns up online.")
    return False, ("Looking up a town means asking one outside website "
                   "(geocoding-api.open-meteo.com). You haven't said yes to "
                   "that yet.")


def allow_geocode(by: str = "owner") -> bool:
    """Record the owner's yes to the town lookup and switch it on.

    Returns False (and changes nothing) when the bot's setup keeps everything
    on this computer: a yes here cannot widen that.
    """
    from puffpai import setup_ops
    from puffpai.security import egress_doors
    if egress_doors.run_posture().get("run_egress") != egress_doors.OPEN_POSTURE:
        return False
    setup_ops.write_main_config({egress_doors.CONFIG_SECTION: {"geocode": True}})
    try:
        # The owner's switch on "What leaves your computer" beats the config
        # file, so a yes typed in the terminal has to be recorded there too.
        from puffpai.security import outside_switches
        outside_switches.set_switch("geocode", True, by="owner")
    except (OSError, ValueError) as exc:
        logger.warning("could not record the town lookup switch: %s", exc)
    egress_doors.reset_cache()
    try:
        from puffpai.security.consent_gate import get_ledger
        get_ledger().approve("outside:geocode", by=by)
    except Exception as exc:  # the switch is the control; the ledger is the record
        logger.warning("could not record consent for the town lookup: %s", exc)
    return egress_doors.egress_allowed(GEOCODE_DOOR)[0]


def next_local(daily_at: str, home: Optional[dict], after: datetime) -> datetime:
    """The next local wall-clock *daily_at* ("17:00") strictly after *after*."""
    hh, mm = (int(x) for x in daily_at.split(":", 1))
    tz, _ = tz_for(home)
    here = after.astimezone(tz)
    cand = here.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if cand <= here:
        cand = (here + timedelta(days=1)).replace(hour=hh, minute=mm, second=0,
                                                  microsecond=0)
    return cand


__all__ = [
    "HomeError", "WHAT_IS_STORED", "get_home", "is_set", "set_home_coords",
    "set_home_town", "lookup_town", "clear_home", "context_line", "local_now",
    "tz_for", "spoken_date", "spoken_time", "round_coord", "geocode_allowed",
    "allow_geocode", "next_local", "note_skipped", "skipped_in_setup",
]
