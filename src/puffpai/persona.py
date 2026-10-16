"""The bot's personality: who it is, how it talks, and what it just learned.

A new build "wakes up" as its own bot. This module makes one small file,
``<state>/persona.json``, from the owner's goal, the bot's name and what it has
installed (skills, tool servers, apps)::

    {name, kind, traits[3], tone, greeting, intro, icon, accent,
     things[], made_with, build, woke_for}

The brain writes it once (thinking off, short timeout). Without a brain, or
when its answer does not fit, a plain fallback is made from the goal's kind
phrase, so there is always a personality.

The intro names what the bot can do, so it stays current: :func:`get` notices
when the bot has gained or lost a thing and rewrites the intro's list at once
(no model call), keeping the personality. :func:`refresh` does the same after
an install: the bot that woke up keeps one identity.

Readers: the chat tool loop's system notes (tone and traits), the chat's first
line, the dashboard header and the wake-up moment (``GET /bot/persona``).

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from typing import Any, Callable, Optional

from puffpai.logging_config import get_logger

logger = get_logger(__name__)

FILE_NAME = "persona.json"
#: Seconds the brain gets to write the personality.
MODEL_TIMEOUT = 45
MAX_THINGS_NAMED = 6

#: Icons the bot may pick (Material Symbols names that exist in the font).
ICONS = (
    "smart_toy", "favorite", "celebration", "cake", "local_florist", "potted_plant",
    "yard", "eco", "restaurant", "menu_book", "school", "fitness_center",
    "directions_run", "savings", "pets", "music_note", "palette", "brush",
    "photo_camera", "flight", "directions_car", "home", "rocket_launch",
    "family_restroom", "child_care", "print", "view_in_ar", "build", "handyman",
    "science", "medical_services", "spa", "sports_esports", "movie", "travel_explore",
    "event", "checklist", "lightbulb", "auto_stories", "hiking", "local_cafe",
    "shopping_cart", "work", "code", "public", "star",
)
#: Accent colours the bot may pick (name -> hex).
ACCENTS = {
    "rose": "#e0607e", "coral": "#f07a5a", "amber": "#e8a33d", "sun": "#d9b526",
    "leaf": "#4caf6a", "teal": "#2aa198", "sky": "#3b9ae1", "indigo": "#5c6bc0",
    "violet": "#8e5cd9", "plum": "#b04a9c", "slate": "#5f7a8a", "copper": "#b8733a",
}

#: Fallback icon and accent by words in the kind phrase or goal.
_THEMES = (
    (("wedding", "bride", "marry", "marriage", "anniversary", "love"), "favorite", "rose"),
    (("party", "birthday", "celebrat", "event"), "celebration", "plum"),
    (("garden", "plant", "tomato", "herb", "flower", "grow", "seed"), "potted_plant", "leaf"),
    (("cook", "recipe", "food", "meal", "bak", "dinner", "kitchen"), "restaurant", "coral"),
    (("3d", "print", "filament"), "view_in_ar", "indigo"),
    (("fitness", "workout", "gym", "run", "train", "exercise"), "fitness_center", "amber"),
    (("money", "budget", "finance", "saving", "tax"), "savings", "teal"),
    (("pet", "dog", "cat", "horse"), "pets", "copper"),
    (("music", "song", "band", "guitar", "piano"), "music_note", "violet"),
    (("book", "read", "study", "learn", "school", "homework"), "menu_book", "sky"),
    (("travel", "trip", "vacation"), "flight", "sky"),
    (("car", "truck", "vehicle"), "directions_car", "slate"),
    (("space", "star", "comet", "planet", "rocket"), "rocket_launch", "indigo"),
    (("baby", "kid", "child", "family"), "family_restroom", "sun"),
    (("art", "paint", "draw", "craft"), "palette", "plum"),
    (("home", "house", "repair", "fix"), "home", "slate"),
)

_ICON_SET = frozenset(ICONS)
_lock = threading.Lock()


# ── where it lives ──────────────────────────────────────────────────────────

def path() -> str:
    from puffpai.config import get_state_dir
    return os.path.join(get_state_dir(create=True), FILE_NAME)


def _read() -> Optional[dict]:
    from puffpai import fsutil
    data = fsutil.read_json(path(), default=None)
    return data if isinstance(data, dict) and data.get("name") else None


def _write(data: dict) -> None:
    from puffpai import fsutil
    fsutil.atomic_write_json(path(), data)


def build_key() -> str:
    """Which build this is: the state folder's id and when the build was made.
    A new build in the same folder gets a new key, so it wakes up again."""
    try:
        from puffpai.security import build_identity
        bid = build_identity.build_id()
    except Exception:  # noqa: BLE001
        bid = ""
    try:
        from puffpai import build_manifest
        made = (build_manifest.read_manifest() or {}).get("generated_at") or ""
    except Exception:  # noqa: BLE001
        made = ""
    if isinstance(made, float):
        made = int(made)
    return f"{bid}:{made}"


# ── what the bot has ────────────────────────────────────────────────────────

def _plain(text: Any, limit: int) -> str:
    out = " ".join(str(text or "").replace("—", ",").replace("–", "-").split())
    out = re.sub(r"(?i)\bdoors?\b", "way", out)
    return out[:limit].strip()


def _build_parts_used() -> "list[str] | None":
    """The names of the parts the build's jobs use, or None with no build
    record (an older bot: everything installed, as before)."""
    try:
        from puffpai.build import schema as build_schema
        if not build_schema.exists():
            return None
        build = build_schema.load_build()
    except Exception:  # noqa: BLE001
        return None
    if not build.get("jobs"):
        return None
    parts = build.get("parts") or {}
    out: list[str] = []
    for job in build["jobs"]:
        # Only what WORKS is named: a job still waiting or not yet working
        # names nothing (an app that never ran is never "learned").
        if job.get("route") == build_schema.D or job.get("status") != build_schema.WORKING:
            continue
        try:
            from puffpai.build import make as build_make
            made = (build_make.load_made(job) or {}).get("made") or {}
        except Exception:  # noqa: BLE001
            made = {}
        if made.get("app_name"):
            # The app that really runs for it (another job's app may be reused).
            name = str(made["app_name"]).strip()
            if name and name not in out:
                out.append(name)
            continue
        for ref in job.get("ingredients") or []:
            part = parts.get(ref) if isinstance(parts.get(ref), dict) else {}
            name = str(part.get("name") or "").strip()
            if part.get("builtin") or part.get("unusable") or not name or name in out:
                continue
            out.append(name)
    return out


def things() -> list[str]:
    """Plain names of what this bot has installed: skills, tool servers, apps.
    A bot with a build record names what its jobs use (never what is under
    "Not used")."""
    used = _build_parts_used()
    if used is not None:
        return used
    names: list[str] = []
    try:
        from puffpai import skills
        from puffpai import things as things_mod
        names += [things_mod.skill_name(r) for r in skills.installed()]
    except Exception:  # noqa: BLE001
        pass
    try:
        from puffpai import mcp_client
        names += [str(r.get("label") or r.get("name") or "") for r in mcp_client.servers()
                  if r.get("name") != "home_assistant"]
    except Exception:  # noqa: BLE001
        pass
    try:
        from puffpai import persona_scaffold
        names += [str(a.get("name") or "") for a in persona_scaffold.owner_apps()
                  if not a.get("own")]
    except Exception:  # noqa: BLE001
        pass
    seen: list[str] = []
    for n in names:
        clean = _plain(n.replace("-", " ").replace("_", " "), 60)
        if clean and clean.lower() not in {s.lower() for s in seen}:
            seen.append(clean)
    return seen


def _list_words(items: list[str]) -> str:
    items = items[:MAX_THINGS_NAMED]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " and " + items[-1]


# ── the fallback (no model) ─────────────────────────────────────────────────

def _theme(text: str) -> tuple[str, str]:
    low = text.lower()
    for words, icon, accent in _THEMES:
        if any(w in low for w in words):
            return icon, ACCENTS[accent]
    return "smart_toy", ACCENTS["sky"]


def _kind_phrase(goal: str) -> str:
    from puffpai import bot_identity
    try:
        subject = bot_identity.topic_phrase(goal, form="subject") if goal else ""
    except Exception:  # noqa: BLE001
        subject = ""
    return f"{subject} helper" if subject else "helper"


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def _names_a_thing(text: str, have: list[str]) -> bool:
    """Does *text* already name one of *have* (the whole name, or two of its
    words in a row, like "Troubleshooting Guide")?"""
    said = _words(text)
    pairs = {tuple(said[i:i + 2]) for i in range(len(said) - 1)}
    for thing in have:
        words = _words(thing)
        if len(words) == 1 and words[0] in said:
            return True
        if any(tuple(words[i:i + 2]) in pairs for i in range(len(words) - 1)):
            return True
    return False


def _kind_case(kind: str) -> str:
    """The kind in lower case, but its proper capitals kept: "3D printing
    assistant", not "3d printing assistant"."""
    return " ".join(w.lower() if w[:1].isupper() and w[1:].islower() and w.isalpha()
                    else w for w in kind.split())


def intro_for(name: str, kind: str, have: list[str], opener: str = "") -> str:
    """The plain intro: who it is (*opener*, the bot's own words, when it has
    them), what it just learned (every thing, by name), let me show you around.
    An opener that already names one of the things is left out, so each thing
    is named once."""
    if opener and _names_a_thing(opener, have):
        opener = ""
    first = opener or f"Hi, I'm {name}, your {kind}."
    if have:
        more = len(have) - MAX_THINGS_NAMED
        extra = f", plus {more} more" if more > 0 else ""
        learned = f" I just learned some new things: {_list_words(have)}{extra}."
    else:
        learned = " I'm brand new, and you can add skills, tools and apps to me any time."
    return first + learned + " Let me show you around."


def fallback(name: str, goal: str, have: list[str]) -> dict:
    """A personality made from the kind phrase alone. Never fails."""
    kind = _kind_phrase(goal)
    icon, accent = _theme(f"{kind} {goal}")
    return {
        "name": name,
        "kind": kind,
        "traits": ["friendly", "helpful", "curious"],
        "tone": "Warm, clear and short, like a friend who is good at this.",
        "greeting": f"Hi! I'm {name}, your {kind}. What shall we do first?",
        "intro": intro_for(name, kind, have),
        "icon": icon,
        "accent": accent,
    }


# ── the brain ───────────────────────────────────────────────────────────────

PROMPT = (
    "You give a new home helper bot its personality. Answer with JSON only, "
    "these keys: kind (2 to 4 plain words for what it is, like \"wedding "
    "planner\"), traits (3 single words), tone (one sentence on how it talks), "
    "greeting (its own short hello, one sentence, says its name), intro (one "
    "short sentence in its own voice that says its name and what kind of "
    "helper it is; never repeat the owner's own words), icon (one of: " + ", ".join(ICONS) + "), accent (one of: "
    + ", ".join(ACCENTS) + "). Plain friendly words. No long dashes.")


def brain_ask(url: str = "", model: str = "") -> Optional[Callable[[list], str]]:
    """One short call to the brain (*url*/*model*, else the configured ones)."""
    try:
        from puffpai.inference.ollama_backend import OllamaBackend
        if not (url and model):
            from puffpai.config import get_config
            cfg = get_config()
            url, model = url or cfg.ollama.url, model or cfg.ollama.model
        backend = OllamaBackend(url=url, model=model, num_ctx=4096, num_predict=500,
                                timeout=MODEL_TIMEOUT, think=False)
    except Exception:  # noqa: BLE001
        return None
    return lambda messages: backend.chat(messages, temperature=0.6, max_tokens=500,
                                         format={"type": "object"})


def _from_model(name: str, goal: str, have: list[str],
                ask: Optional[Callable[[list], str]]) -> Optional[dict]:
    if ask is None:
        return None
    user = json.dumps({"name": name, "goal": goal[:300], "new_things": have[:12]})
    try:
        answer = str(ask([{"role": "system", "content": PROMPT},
                          {"role": "user", "content": user}]) or "")
    except Exception:  # noqa: BLE001 - no model, no change
        return None
    start, end = answer.find("{"), answer.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        got = json.loads(answer[start:end + 1])
    except ValueError:
        return None
    return got if isinstance(got, dict) else None


def _echoes(text: str, goal: str, run: int = 4) -> bool:
    """Does *text* repeat four words in a row of the owner's goal? The bot
    must not say her own story as if it were its own ("My cousin is ...")."""
    said = re.findall(r"[a-z']+", text.lower())
    heard = re.findall(r"[a-z']+", goal.lower())
    runs = {tuple(heard[i:i + run]) for i in range(len(heard) - run + 1)}
    return any(tuple(said[i:i + run]) in runs for i in range(len(said) - run + 1))


def opener_of(intro: Any, name: str, goal: str) -> str:
    """The first sentence of the model's intro when it says who the bot is, in
    the bot's voice, else "". The rest is not kept: what the bot learned is
    always its real things, by name (else the model names jobs nothing
    installed can do, and leaves the new things out)."""
    first = re.split(r"(?<=[.!?])\s+", _plain(intro, 600))[0].strip()
    if not first or len(first) > 160 or name.lower() not in first.lower():
        return ""
    if _echoes(first, goal) or "show you around" in first.lower():
        return ""
    return first if first[-1] in ".!?" else first + "."


def _merge(base: dict, got: Optional[dict], goal: str = "",
           have: Optional[list] = None) -> tuple[dict, bool]:
    """*base* with every field the model gave that fits. (persona, used_model)."""
    if not got:
        return base, False
    out = dict(base)
    used = False
    kind = _plain(got.get("kind"), 40)
    if kind and len(kind.split()) <= 5:
        out["kind"] = _kind_case(kind)
        used = True
    traits = got.get("traits")
    if isinstance(traits, list):
        words = [_plain(t, 20).lower() for t in traits if _plain(t, 20)]
        words = [w for w in words if " " not in w][:3]
        if len(words) == 3:
            out["traits"] = words
            used = True
    if out["kind"] != base["kind"]:
        out["greeting"] = f"Hi! I'm {out['name']}, your {out['kind']}. What shall we do first?"
    for key, limit in (("tone", 200), ("greeting", 200)):
        text = _plain(got.get(key), limit)
        if text and len(text) > 8 and not _echoes(text, goal):
            out[key] = text
            used = True
    opener = opener_of(got.get("intro"), out["name"], goal)
    if opener:
        out["opener"] = opener
        used = True
    out["intro"] = intro_for(out["name"], out["kind"], list(have or []), opener)
    icon = str(got.get("icon") or "").strip()
    if icon in _ICON_SET:
        out["icon"] = icon
    accent = str(got.get("accent") or "").strip().lower()
    if accent in ACCENTS:
        out["accent"] = ACCENTS[accent]
    if used and out["name"].lower() not in out["greeting"].lower():
        out["greeting"] = f"Hi! I'm {out['name']}. " + out["greeting"]
    return out, used


# ── make, read, refresh ─────────────────────────────────────────────────────

def make(*, ask: Any = "default", keep_woke: bool = True) -> dict:
    """Make the personality now and save it. *ask* is the brain (None for none)."""
    from puffpai import bot_identity
    name = bot_identity.bot_name()
    goal = bot_identity.bot_goal()
    have = things()
    if ask == "default":
        ask = brain_ask()
    persona, used = _merge(fallback(name, goal, have), _from_model(name, goal, have, ask),
                           goal, have)
    with _lock:
        old = _read() or {}
        persona.update({
            "things": have,
            "made_with": "brain" if used else "fallback",
            "made_at": time.time(),
            "build": build_key(),
            "woke_for": old.get("woke_for", "") if keep_woke else "",
        })
        _write(persona)
    return persona


def refresh(*, ask: Any = "default") -> dict:
    """After new skills, tools or apps: only the "what I can do" list and the
    intro naming it change. The bot that woke up keeps its name, kind, icon,
    accent, traits, tone and greeting (a new thing never changes its icon and
    its hello). With no personality yet, one is made."""
    persona = _read()
    if persona is None:
        return make(ask=ask)
    return _with_current_things(persona)


def _with_current_things(persona: dict) -> dict:
    """*persona* with its intro naming what the bot has NOW. Saves if changed."""
    from puffpai import bot_identity
    have = things()
    name = bot_identity.bot_name()
    if have == persona.get("things") and name == persona.get("name"):
        return persona
    opener = str(persona.get("opener") or "") if name == persona.get("name") else ""
    persona = dict(persona, name=name, things=have,
                   intro=intro_for(name, persona.get("kind") or "helper", have, opener))
    if name.lower() not in str(persona.get("greeting") or "").lower():
        persona["greeting"] = f"Hi! I'm {name}, your {persona.get('kind') or 'helper'}."
    with _lock:
        _write(persona)
    return persona


def get(*, ask: Any = "default") -> dict:
    """The personality, made now if there is none, its intro kept current."""
    persona = _read()
    if persona is None:
        return make(ask=ask)
    return _with_current_things(persona)


def stored() -> Optional[dict]:
    """The saved personality, or None. Never makes one, never raises."""
    try:
        return _read()
    except Exception:  # noqa: BLE001
        return None


def public(persona: dict) -> dict:
    """What GET /bot/persona returns."""
    key = build_key()
    return {
        "name": persona.get("name", ""),
        "kind": persona.get("kind", ""),
        "traits": list(persona.get("traits") or []),
        "tone": persona.get("tone", ""),
        "greeting": persona.get("greeting", ""),
        "intro": persona.get("intro", ""),
        "icon": persona.get("icon", "smart_toy"),
        "accent": persona.get("accent", ACCENTS["sky"]),
        "things": list(persona.get("things") or []),
        "made_with": persona.get("made_with", "fallback"),
        "build": key,
        "wake": persona.get("woke_for") != key,
    }


def mark_woke() -> dict:
    """Remember the wake-up was shown for this build."""
    persona = get(ask=None)
    with _lock:
        persona = dict(persona, woke_for=build_key())
        _write(persona)
    return persona


# ── readers elsewhere ───────────────────────────────────────────────────────

def _things_rows() -> list[dict]:
    """What the bot has, from the things list: ``[{name, blurb}]``."""
    try:
        from puffpai import things as things_mod
        return [{"name": str(r.get("name") or ""), "blurb": _plain(r.get("blurb"), 160)}
                for r in things_mod.listing() if r.get("name")]
    except Exception:  # noqa: BLE001 - a broken list names nothing
        return []


def prompt_note() -> str:
    """The chat loop's system note on how this bot talks and what it has, or ""."""
    p = stored()
    if not p:
        return ""
    traits = ", ".join(str(t) for t in (p.get("traits") or []) if t)
    parts = [f"You are {p.get('name')}, a {p.get('kind') or 'helper'}."]
    if traits:
        parts.append(f"Your personality: {traits}.")
    if p.get("tone"):
        parts.append(f"How you talk: {p['tone']}")
    have = "; ".join(f"{r['name']} ({r['blurb']})" if r["blurb"] else r["name"]
                     for r in _things_rows())
    parts.append(f"The things you have: {have}." if have else "You have no skills or tools yet.")
    parts.append("When asked who you are or what you can do, name only these things and "
                 "what they do. Never claim a job none of them does.")
    return " ".join(parts)


#: Ways to ask what the bot can do (whole questions; see :func:`asks_about_me`).
_CAN_PATTERNS = (
    r"^\s*what (?:else )?(?:can|could) you (?:do|help (?:me )?with)(?: for me)?\s*$",
    r"^\s*what are you (?:able to do|good at)\s*$",
    r"^\s*how (?:can|could) you help(?: me)?\s*$",
    r"^\s*what (?:are|is) your (?:skills|abilities|capabilities)\s*$",
)


def asks_about_me(text: Any) -> bool:
    """True when the WHOLE message asks who the bot is or what it can do
    ("Hi! Who are you, and what can you help me with?")."""
    from puffpai import bot_identity
    lowered = str(text or "").lower().replace("\u2019", "'")
    clauses = [c for c in re.split(r"\?|!|\.|,|;|\band\b|\balso\b", lowered) if c.strip()]
    if not clauses:
        return False
    hits = [bot_identity.asks_who(c) or any(re.search(p, c) for p in _CAN_PATTERNS)
            for c in clauses]
    return any(hits) and all(h or bot_identity._FILLER.match(c)  # noqa: SLF001
                             for h, c in zip(hits, clauses))


def about_me() -> str:
    """Who the bot is and what it can do: its own first line, then every thing
    it has (from the things list), by name. "" without a personality."""
    p = stored()
    if not p:
        return ""
    name = str(p.get("name") or "")
    first = str(p.get("opener") or "") or f"Hi, I'm {name}, your {p.get('kind') or 'helper'}."
    rows = _things_rows()
    if not rows:
        return (f"{first} I'm brand new, so I have no skills or tools yet. Tell me "
                "\"find me\" and what you need, and I will look.")
    lines = [first, "", "Here is what I can do with the things I have:"]
    lines += [f"- **{r['name']}**: {r['blurb']}" if r["blurb"] else f"- **{r['name']}**"
              for r in rows]
    lines += ["", "Each one has its own page from your home page. Want more? Tell me "
                  "\"find me\" and what you need."]
    return "\n".join(lines)


def chat_greeting(default: str) -> str:
    """The chat's first line: the bot's own hello and the privacy promise."""
    p = stored()
    hello = str((p or {}).get("greeting") or "").strip()
    if not hello:
        return default
    return f"{hello} Nothing leaves your computer without your consent."


def refresh_soon() -> None:
    """Remake the personality in the background (after an install)."""
    def _run() -> None:
        try:
            if stored() is not None:
                refresh()
        except Exception as exc:  # noqa: BLE001
            logger.debug("persona refresh failed: %s", exc)
    threading.Thread(target=_run, name="persona-refresh", daemon=True).start()


__all__ = ["ACCENTS", "ICONS", "about_me", "asks_about_me", "build_key", "chat_greeting",
           "fallback", "get",
           "intro_for", "make", "mark_woke", "prompt_note", "public", "refresh",
           "refresh_soon", "stored", "things"]
