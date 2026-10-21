"""The built-in weather service: one reader for chat, the home page and the wizard.

Most people do not have Home Assistant, and there are other ways to get the
weather. A space bot could have the weather card on its page list, the
weather service switched off, and lines that send the owner to Home
Assistant. None of that is needed: BuildBot ships a
keyless weather service (Open-Meteo, the ``weather`` outside service in
:mod:`puffpai.security.egress_doors`) and a town-level home
(:mod:`puffpai.home_location`). This module is the one place that joins them.

* :func:`state` says whether the weather can be read right now and, when it
  cannot, why in plain words: the bot keeps everything on the computer, the
  service is switched off, or the bot does not know its town yet. Evidence or
  silence: a shut service is said to be shut, never "needs Home Assistant".
* :func:`forecast` makes ONE GET through the ``weather`` service, with the
  town's rough position (one decimal, about 11 km) and nothing else.
* :func:`chat_answer` answers "what's the weather tomorrow?" from it, before
  the model, so a number is never made up.
* :func:`goal_fits` tells the wizard whether the owner's goal is one the
  weather card belongs to, from the capability catalog (data, not code): the
  same rule that puts the weather card on the page list.

Nothing here knows a topic. The words are weather words, the same way the
clock's words are time words.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any, Callable, Optional

from puffpai.logging_config import get_logger

logger = get_logger(__name__)

DOOR = "weather"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
HOST = "api.open-meteo.com"
#: The page the forecast's switch lives on (the one "switch it on" place).
PAGE = "What leaves your computer"
#: How far ahead Open-Meteo's free forecast is asked for.
MAX_DAYS = 7
#: The page a weather card sits on, in the capability catalog.
WEATHER_PAGE = "daily.weather"

OK = "ok"
OFFLINE_BOT = "offline_bot"
SWITCHED_OFF = "switched_off"
NO_TOWN = "no_town"


class WeatherError(RuntimeError):
    """The service was asked and did not give a forecast. ``str()`` is plain."""


# ---------------------------------------------------------------------------
# can it be read right now?
# ---------------------------------------------------------------------------

def service_name() -> str:
    """The forecast's name on the What leaves your computer page."""
    try:
        from puffpai import outside_services
        return str(outside_services._BUILT_IN[DOOR]["name"])  # noqa: SLF001
    except Exception:  # noqa: BLE001
        return "Weather forecast"


def switch_on_words(surface: str = "chat") -> str:
    """How to switch the forecast on, worded for ``surface``."""
    from puffpai import plain_words
    return plain_words.render_action(
        {"id": "outside.on", "args": {"name": service_name()}}, surface)


def is_on() -> bool:
    """Is the weather forecast switched on for this bot right now?"""
    try:
        from puffpai.security import egress_doors
        return bool(egress_doors.egress_allowed(DOOR)[0])
    except Exception:  # noqa: BLE001 - unreadable means shut
        return False


def has_card() -> bool:
    """Does this build's home page carry the weather card (where the town is
    asked)? An untrimmed build carries every card, and so does any bot with
    the weather switched on, whatever its page list says: the card is where
    the town is typed."""
    try:
        from puffpai import build_abilities
        pages = build_abilities.page_list()
    except Exception:  # noqa: BLE001
        pages = []
    return pages is None or WEATHER_PAGE in pages or is_on()


def town_words(surface: str = "chat") -> str:
    """How to tell the bot its town, worded for ``surface``."""
    from puffpai import plain_words
    act = "home.set_card" if has_card() else "home.set"
    return plain_words.render_action({"id": act, "args": {}}, surface)


def state(surface: str = "chat") -> dict[str, Any]:
    """``{"state", "message", "home"}``: can the forecast be read right now?

    ``message`` is empty when it can. Several things can be missing at once
    (switched off AND no town); the message names each, the switch first.
    """
    home = None
    try:
        from puffpai import home_location
        home = home_location.get_home()
    except Exception:  # noqa: BLE001 - unreadable home is "not set"
        home = None
    try:
        from puffpai.security import egress_doors
        allowed, _why = egress_doors.egress_allowed(DOOR)
        offline = (egress_doors.run_posture().get("run_egress")
                   != egress_doors.OPEN_POSTURE)
    except Exception:  # noqa: BLE001 - unreadable means shut
        allowed, offline = False, False
    if offline and not allowed:
        return {"state": OFFLINE_BOT, "home": home,
                "message": "This bot is set to keep everything on your "
                           "computer, so it does not check the weather."}
    parts: list[str] = []
    code = OK
    if not allowed:
        code = SWITCHED_OFF
        parts.append(f"The weather forecast is switched off, so I can't check "
                     f"it. {switch_on_words(surface)}")
    if home is None:
        code = code if code != OK else NO_TOWN
        parts.append("I don't know your home town yet, so I don't know where "
                     "to check. " + town_words(surface))
    if parts:
        parts.append(f"What leaves your computer for the forecast: only your "
                     f"town's rough location (to about 11 km), sent to {HOST}. "
                     f"No sign-up needed.")
    return {"state": code, "home": home, "message": " ".join(parts)}


def card_without_town(out: Optional[dict] = None) -> dict[str, Any]:
    """The home page weather card's answer while no town is set.

    Nothing is fetched: there is no place to ask about, and the old fallback
    (a fixed spot in Greenwich) showed somebody else's weather. The card asks
    for the town instead and says what that sends.
    """
    ready = state("browser")
    try:
        from puffpai import home_location
        stored = home_location.WHAT_IS_STORED
        lookup_ok, lookup_note = home_location.geocode_allowed()
        skipped = home_location.skipped_in_setup()
    except Exception:  # noqa: BLE001
        stored, lookup_ok, lookup_note, skipped = "", False, "", False
    has_temp = bool(out and out.get("temp") is not None)
    return {"available": has_temp, "needs_home": True,
            "weather_on": ready["state"] != SWITCHED_OFF
                          and ready["state"] != OFFLINE_BOT,
            "reason": "no_town", "message": ready["message"],
            "what_is_stored": stored,
            "town_lookup_allowed": bool(lookup_ok),
            "town_lookup_note": lookup_note,
            "town_lookup_sends": "Only the town name you type, sent once to "
                                 "geocoding-api.open-meteo.com.",
            # Said plainly on the card when the owner skipped the town in
            # the wizard: what to do now, and that nothing was sent.
            "town_later": ("You skipped your town when this bot was set "
                           "up, so there is no weather yet. Type your town "
                           "below whenever you like, and the weather card "
                           "and weather questions in chat start working."
                           if skipped else ""),
            "offline_bot": ready["state"] == OFFLINE_BOT}


# ---------------------------------------------------------------------------
# the one GET
# ---------------------------------------------------------------------------

def _fahrenheit(home: dict) -> bool:
    """Fahrenheit for a town in the United States, Celsius everywhere else."""
    # The town lookup labels a place "Town, State, Country".
    return str(home.get("label") or "").rstrip().endswith("United States")


def unit_for(home: dict) -> str:
    """``"fahrenheit"`` or ``"celsius"`` for this home (the service's words)."""
    return "fahrenheit" if _fahrenheit(home) else "celsius"


def fill_now(out: dict) -> dict:
    """The card's "now" from the weather service, where no hub gave one.

    ``out["now"]`` is what :func:`puffpai.dashboard_widgets.fetch_environment`
    read in its one request. A Home Assistant reading, when there is one,
    stays: it is a sensor at the house. Without one the card still shows the
    temperature now, instead of "--" and a note about a hub most people
    don't have.
    """
    now = out.pop("now", None) or {}
    for key in ("temp", "feels_like", "humidity"):
        if out.get(key) is None and isinstance(now.get(key), (int, float)):
            out[key] = round(now[key])
    if not out.get("condition"):
        out["condition"] = sky_words(now.get("code")) or None
    # A hub that is not there is not an error to show anyone.
    if out.get("temp") is not None or not _hub_configured():
        out.pop("ha_error", None)
    return out


def _hub_configured() -> bool:
    try:
        from puffpai.config import get_config
        ha = get_config().ha
        if not (ha and ha.enabled):
            return False
        if ha.token:
            return True
        from puffpai.security import keys
        return keys.has_house_key("home_assistant")
    except Exception:  # noqa: BLE001
        return False


def forecast(days: int = 3, *, home: Optional[dict] = None,
             getter: Optional[Callable[..., Any]] = None) -> dict[str, Any]:
    """Daily forecast for home: ``{"days": [...], "unit", "label"}``.

    One GET through the ``weather`` outside service, carrying the town's
    rounded position and nothing else. Raises :class:`WeatherError` with a
    plain sentence when the service is shut, the town is unknown or the
    service does not answer; ``EgressBlocked`` is never let through raw.
    """
    from puffpai.security import egress_doors
    if home is None:
        from puffpai import home_location
        home = home_location.get_home()
    if not home:
        raise WeatherError("I don't know your home town yet.")
    days = max(1, min(MAX_DAYS, int(days)))
    unit = "fahrenheit" if _fahrenheit(home) else "celsius"
    fetch = getter or egress_doors.egress_get
    try:
        resp = fetch(DOOR, FORECAST_URL, params={
            "latitude": round(float(home["latitude"]), 1),
            "longitude": round(float(home["longitude"]), 1),
            "daily": ("weather_code,temperature_2m_max,temperature_2m_min,"
                      "precipitation_probability_max"),
            "temperature_unit": unit, "timezone": "auto",
            "forecast_days": days})
        data = resp.json()
    except egress_doors.EgressBlocked as blocked:
        raise WeatherError(blocked.user_message or
                           "The weather forecast is switched off.")
    except Exception as exc:  # noqa: BLE001 - network or service trouble
        logger.info("weather lookup failed: %s", type(exc).__name__)
        raise WeatherError("The weather service did not answer just now. "
                           "Please try again in a minute.")
    daily = data.get("daily") if isinstance(data, dict) else None
    if not isinstance(daily, dict):
        raise WeatherError("The weather service answered with nothing I "
                           "could read.")
    out: list[dict[str, Any]] = []
    stamps = daily.get("time") or []
    for i, stamp in enumerate(stamps[:days]):
        try:
            when = date.fromisoformat(str(stamp))
        except ValueError:
            continue
        out.append({"date": when,
                    "code": _at(daily.get("weather_code"), i),
                    "high": _at(daily.get("temperature_2m_max"), i),
                    "low": _at(daily.get("temperature_2m_min"), i),
                    "rain": _at(daily.get("precipitation_probability_max"), i)})
    if not out:
        raise WeatherError("The weather service answered with nothing I "
                           "could read.")
    return {"days": out, "unit": "F" if unit == "fahrenheit" else "C",
            "label": str(home.get("label") or "your town")}


def _at(values: Any, i: int) -> Optional[float]:
    if isinstance(values, list) and i < len(values):
        v = values[i]
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return float(v)
    return None


#: WMO weather codes, as Open-Meteo reports them, in plain words.
_SKY: tuple[tuple[range, str], ...] = (
    (range(0, 1), "clear skies"), (range(1, 3), "partly cloudy"),
    (range(3, 4), "cloudy"), (range(45, 49), "fog"),
    (range(51, 58), "drizzle"), (range(61, 68), "rain"),
    (range(71, 78), "snow"), (range(80, 83), "rain showers"),
    (range(85, 87), "snow showers"), (range(95, 100), "thunderstorms"))


def sky_words(code: Optional[float]) -> str:
    if code is None:
        return ""
    for span, words in _SKY:
        if int(code) in span:
            return words
    return ""


def day_line(day: dict[str, Any], unit: str) -> str:
    """"partly cloudy, high 72°F, low 55°F, 40% chance of rain"."""
    bits = [sky_words(day.get("code"))]
    if day.get("high") is not None:
        bits.append(f"high {round(day['high'])}°{unit}")
    if day.get("low") is not None:
        bits.append(f"low {round(day['low'])}°{unit}")
    if day.get("rain") is not None:
        bits.append(f"{round(day['rain'])}% chance of rain")
    return ", ".join(b for b in bits if b)


# ---------------------------------------------------------------------------
# chat: "what's the weather tomorrow?"
# ---------------------------------------------------------------------------

_WEATHER_WORD = re.compile(
    r"(?i)\b(?:weather|forecast|rain(?:ing|y)?|snow(?:ing|y)?|temperature|"
    r"umbrella|sunny|windy|stormy|freez(?:e|ing)|frost|"
    r"how\s+(?:hot|cold|warm)\s+(?:is|will)\s+it)\b")
#: The shapes of a question about the weather HERE, now or soon.
_LOCAL = re.compile(
    r"(?ix)\b(?:today|tonight|tomorrow|this\s+(?:morning|afternoon|evening|"
    r"week|weekend)|weekend|right\s+now|outside|here|monday|tuesday|wednesday|"
    r"thursday|friday|saturday|sunday)\b"
    r"|\bthe\s+(?:weather|forecast)\b|\bweather\s+like\b|\bforecast\b"
    r"|\b(?:will|is)\s+it\s+(?:going\s+to\s+)?(?:be\s+)?(?:rain|snow|freez|"
    r"frost|sunny|windy|hot|cold|warm)"
    r"|\bdo\s+i\s+need\s+(?:an?\s+)?umbrella\b"
    r"|\bhow\s+(?:hot|cold|warm)\s+(?:is|will)\s+it\b")
#: Not about the weather at home: somewhere else, another world, or how
#: weather works (a library question for the model and its books).
_ELSEWHERE = re.compile(
    r"(?ix)\bspace\s+weather\b|\bsolar\b|\bclimate\b|\bpatterns?\b"
    r"|\bwhy\s+(?:does|do|is|are)\b|\bhow\s+(?:does|do)\b|\bwhat\s+causes\b"
    r"|\bexplain\b|\bhistory\b"
    r"|\b(?:weather|forecast|rain|snow)\s+(?:in|on|at|for)\s+"
    r"(?!(?:my|our|here|home|today|tonight|tomorrow|this|the\s+week"
    r"|the\s+weekend|monday|tuesday|wednesday|thursday|friday|saturday"
    r"|sunday)\b)")

_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday",
             "saturday", "sunday")


def is_weather_question(text: Any) -> bool:
    """True for a question about the weather at home, today or soon."""
    msg = " ".join(str(text or "").split())
    if not msg or len(msg) > 200:
        return False
    if not _WEATHER_WORD.search(msg) or _ELSEWHERE.search(msg):
        return False
    return bool(_LOCAL.search(msg))


def which_days(text: Any, today: date) -> tuple[list[int], str]:
    """Day offsets from today the question is about, and how to name them."""
    msg = str(text or "").lower()
    if re.search(r"\bweekend\b", msg):
        sat = (5 - today.weekday()) % 7
        return [sat, sat + 1], "this weekend"
    if re.search(r"\bthis\s+week\b|\bnext\s+few\s+days\b|\bweek\b", msg):
        return list(range(0, 5)), "the next few days"
    if "tomorrow" in msg:
        return [1], "tomorrow"
    for i, name in enumerate(_WEEKDAYS):
        if re.search(rf"\b{name}\b", msg):
            ahead = (i - today.weekday()) % 7
            return [ahead], ("today" if ahead == 0 else name.capitalize())
    if "tonight" in msg:
        return [0], "tonight"
    return [0], "today"


def chat_answer(message: Any, *, surface: str = "chat",
                getter: Optional[Callable[..., Any]] = None,
                now: Optional[datetime] = None) -> Optional[str]:
    """The answer to a weather question, or None when it is not one.

    Asked when the service is shut or the town unknown, it says which and how
    to fix it. Asked when both are ready, it makes one GET and reads the
    forecast back. It never guesses a number.
    """
    if not is_weather_question(message):
        return None
    ready = state(surface)
    if ready["state"] != OK:
        return ready["message"]
    home = ready["home"]
    try:
        from puffpai import home_location
        today = home_location.local_now(home, now).date()
    except Exception:  # noqa: BLE001
        today = (now or datetime.now()).date()
    offsets, named = which_days(message, today)
    if max(offsets) >= MAX_DAYS:
        return f"I can only see the forecast {MAX_DAYS} days ahead."
    try:
        found = forecast(max(3, max(offsets) + 1), home=home, getter=getter)
    except WeatherError as exc:
        return str(exc)
    by_date = {d["date"]: d for d in found["days"]}
    lines: list[str] = []
    for off in offsets:
        day = by_date.get(today + timedelta(days=off))
        if day is None:
            continue
        name = ("Today" if off == 0 else "Tomorrow" if off == 1
                else day["date"].strftime("%A"))
        lines.append(f"{name}: {day_line(day, found['unit'])}.")
    if not lines:
        return "The weather service did not have that day yet."
    head = f"The weather for {named} in {found['label']}"
    if len(lines) == 1:
        text = f"{head}: {lines[0].split(': ', 1)[1]}"
    else:
        text = head + ":\n" + "\n".join(f"- {line}" for line in lines)
    return (text + f"\n(From the weather service at {HOST}, for your town's "
            f"rough area.)")


# ---------------------------------------------------------------------------
# the wizard: does the goal want the weather?
# ---------------------------------------------------------------------------

def goal_fits(goal: Any) -> bool:
    """True when the goal matches a capability whose page is the weather card.

    The capability catalog's own tags decide ("frost", "garden", "forecast"),
    the same rule that puts the weather card on the build's page list, so the
    card and the service it needs are proposed together.
    """
    text = str(goal or "").strip()
    if not text:
        return False
    try:
        from puffpai import build_interview
        flat = build_interview.propose(text).get("flat") or []
    except Exception:  # noqa: BLE001 - no match is "does not fit"
        return False
    return any(WEATHER_PAGE in (item.get("surfaces") or [])
               for item in flat if isinstance(item, dict))


def needs_hub(goal: Any) -> bool:
    """True when the goal matches a capability that only a home hub provides.

    Lights, plugs, thermostats, locks, cameras and home sensors: the catalog
    items whose pages are ``house.*``. Only then is Home Assistant offered.
    """
    text = str(goal or "").strip()
    if not text:
        return False
    try:
        from puffpai import build_interview
        flat = build_interview.propose(text).get("flat") or []
    except Exception:  # noqa: BLE001
        return False
    return any(str(s).startswith("house.")
               for item in flat if isinstance(item, dict)
               for s in (item.get("surfaces") or []))


__all__ = [
    "DOOR", "FORECAST_URL", "HOST", "NO_TOWN", "OFFLINE_BOT", "OK",
    "SWITCHED_OFF", "WeatherError", "card_without_town", "chat_answer",
    "day_line", "fill_now", "forecast", "has_card", "is_on", "unit_for",
    "goal_fits", "is_weather_question", "needs_hub", "service_name",
    "sky_words", "state", "switch_on_words", "town_words", "which_days",
]
