"""Test stand-ins for the build pipeline: a scripted brain, a concept
embedder (a few hand-made meaning groups instead of all-MiniLM) and helpers
to write fake candidates. Nothing here goes online.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import json
import math
from typing import Any, Callable, Optional

from puffpai.build.decide import cand_text, stem, words_of

CONCEPTS: dict[str, set[str]] = {
    "music": {"music", "song", "melody", "tune", "radio", "station", "playlist", "album"},
    "listen": {"listen", "play", "stream", "audio", "sound", "hear"},
    "make": {"make", "create", "generate", "compose"},
    "learn": {"learn", "lesson", "teach", "study", "course", "practice", "explain", "basic"},
    "theory": {"theory", "chord", "scale", "harmony"},
    "piano": {"piano", "keyboard", "key"},
    "space": {"space", "nasa", "astronomy", "planet", "rocket", "star", "constellation", "sky",
              "celestial", "universe", "galaxy", "satellite", "orbit", "orbital", "telescope"},
    "earth": {"earth", "globe"},
    "picture": {"picture", "photo", "image", "gallery", "diagram", "imagery"},
    "event": {"event", "launch", "upcoming", "tonight", "phenomena", "visible"},
    "news": {"news", "latest", "update", "discovery", "briefing"},
    "place": {"near", "map", "location", "venue", "place", "where", "local", "area", "nearby",
              "viewing"},
    "facts": {"fact", "information", "encyclopedia", "article", "knowledge", "question",
              "answer", "textbook", "comprehensive"},
    "wedding": {"wedding", "guest", "bride", "ceremony", "rsvp"},
    "keep": {"keep", "manage", "track", "organize", "checklist"},
    "remind": {"remind", "reminder", "routine", "alarm", "task"},
    "garden": {"garden", "plant", "water", "watering", "flower", "soil"},
    "weather": {"weather", "forecast", "rain", "temperature"},
    "recipe": {"recipe", "cook", "cooking", "meal", "dish", "ingredient", "cookbook"},
    "shopping": {"shopping", "grocery", "buy", "store"},
    "money": {"money", "spending", "budget", "bank", "expense", "transaction", "finance",
              "account", "household"},
    "time": {"history", "trend", "chart", "month", "hour", "week"},
    "study": {"study", "flashcard", "quiz", "exam", "note", "memorize", "repetition"},
    "design": {"design", "brand", "interface", "pattern", "layout", "visual", "style",
               "marketing", "copy", "sketch", "template", "filter", "theme"},
    "fortune": {"horoscope", "fortune", "zodiac", "astrology"},
    "trade": {"crypto", "trading", "coin", "exchange"},
    "identify": {"identify", "recognize", "detect", "tell"},
}
_STEMMED = {k: {stem(w) for w in v} for k, v in CONCEPTS.items()}
NAMES = list(CONCEPTS)


def concept_embed(texts: list[str]) -> list[list[float]]:
    out = []
    for t in texts:
        ws = words_of(t)
        vec = [1.0 if ws & _STEMMED[k] else 0.0 for k in NAMES]
        n = math.sqrt(sum(vec)) or 1.0
        out.append([v / n for v in vec])
    return out


class ScriptedBrain:
    """``brain(messages, schema) -> str``. Interpret calls answer from
    *jobs_by_goal*; judge calls answer "no" for (job, thing) pairs the
    candidate lists in its ``judge_no`` ("*" = every job), else "yes"."""

    def __init__(self, jobs_by_goal: Optional[dict] = None, cands: Any = (),
                 bad_first: int = 0, judge: Optional[Callable[[str, str], str]] = None):
        self.jobs_by_goal = dict(jobs_by_goal or {})
        self.by_text = {cand_text(c)[:300]: c for c in cands}
        self.bad_first = bad_first
        self.judge_fn = judge
        self.calls: list[str] = []

    def add_cands(self, cands: Any) -> None:
        self.by_text.update({cand_text(c)[:300]: c for c in cands})

    def __call__(self, messages: list, fmt: dict) -> str:
        system = messages[0]["content"]
        user = json.loads(messages[1]["content"])
        if system.startswith("TASK=interpret"):
            self.calls.append("interpret")
            if self.bad_first > 0:
                self.bad_first -= 1
                return json.dumps({"jobs": [{"name": "x", "action": "dance", "output": "?",
                                             "needs": ["moon"], "subject": [], "does": "",
                                             "ask": ""}]})
            return json.dumps({"jobs": self.jobs_by_goal.get(user["goal"], [])})
        if system.startswith("TASK=judge"):
            self.calls.append("judge")
            answers = []
            pairs = user.get("pairs") or [{"i": x["i"], "thing": x["thing"], "job": user["job"]}
                                         for x in user.get("things") or []]
            for p in pairs:
                job = p["job"].split(":", 1)[0].strip()
                if self.judge_fn is not None:
                    fits = self.judge_fn(job, p["thing"])
                else:
                    c = self.by_text.get(p["thing"]) or {}
                    no = c.get("judge_no") or []
                    fits = "no" if ("*" in no or job in no) else "yes"
                answers.append({"i": p["i"], "fits": fits})
            return json.dumps({"answers": answers})
        return "[Error] unknown task"


# ── candidates ─────────────────────────────────────────────────────────────

def app(ref, name, what, kind="browser_app", **kw):
    return {"ref": ref, "name": name, "type": kind, "what": what, "hosts": [], "needs": [], **kw}


def mcp(ref, name, what, url, tools, **kw):
    host = url.split("/")[2]
    return {"ref": ref, "name": name, "type": "mcp", "what": what, "mcp": {"url": url},
            "tools": tools, "hosts": [host], "needs": [], **kw}


def cli_skill(ref, name, what, bins, **kw):
    md = (f"---\nname: {ref.rsplit(':', 1)[-1]}\ndescription: {what}\n"
          f'metadata: {{"openclaw": {{"requires": {{"bins": {json.dumps(bins)}}}}}}}\n---\n'
          f"# {name}\n\n{what}\n\n```bash\npython scripts/run.py \"a calm song\"\n```\n")
    return {"ref": ref, "name": name, "type": "skill", "what": what, "skill_md": md,
            "files": ["SKILL.md", "scripts/run.py"], "hosts": [], "needs": [], **kw}


def api_skill(ref, name, what, host, **kw):
    md = (f"---\nname: {ref.rsplit('/', 1)[-1]}\ndescription: {what}\n---\n# {name}\n\n"
          f"Call https://{host}/v1/index with your key.\n")
    return {"ref": ref, "name": name, "type": "skill", "what": what, "skill_md": md,
            "files": ["SKILL.md", "scripts/gen.py"], "hosts": [], "needs": [], **kw}


def guide(ref, name, what, **kw):
    md = f"---\nname: {ref.rsplit('/', 1)[-1]}\ndescription: {what}\n---\n# {name}\n\n{what}\n"
    return {"ref": ref, "name": name, "type": "skill", "what": what, "skill_md": md,
            "files": ["SKILL.md"], "hosts": [], "needs": [], **kw}


def live(ref, name, what, host, needs=(), auth="none", **kw):
    return {"ref": ref, "name": name, "type": "live_data", "what": what, "hosts": [host],
            "needs": list(needs), "auth": auth, "service": ref, **kw}


def book(ref, name, what, **kw):
    return {"ref": ref, "name": name, "type": "book", "what": what, "hosts": [], "needs": [],
            **kw}


def job(name, does, action, output, needs, subject):
    return {"name": name, "does": does, "action": action, "output": output, "needs": needs,
            "subject": subject, "ask": f"{name}?"}
