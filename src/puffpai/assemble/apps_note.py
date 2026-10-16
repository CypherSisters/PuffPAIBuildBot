"""What the chat's brain is told about the apps BuildBot put together.

Three things, all read fresh from the specs and the proof report:

* :func:`tool_description` -- each ``<app>__<action>`` tool says which job it
  does and what it gives back, with the source's own labels and what its
  values mean ("Cloud cover: 1 clear to 9 overcast"), so a small model does
  not read a 1 as cloudy;
* :func:`block` -- the "Your apps" system note: per proven job, the tool to
  call and what it returns;
* :func:`nudge` -- when her question is close in meaning to a proven job
  (all-MiniLM, the same reading helper the finds use), one line telling the
  brain to call that job's tool first instead of answering from memory.

Only proven apps (report ``working``) are named. Goal-agnostic.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

from typing import Any, Optional

from puffpai.logging_config import get_logger

logger = get_logger(__name__)

PROVIDER = "assembled"
DESCRIPTION_MAX = 700
BLOCK_HEAD = ("## Your apps\n"
              "Apps put together for this bot's jobs. For one of these jobs, call its tool "
              "first and answer from what it gives back, never from memory.\n")
NUDGE = ("Her question is about the job \"{job}\". Call {tool} first and answer from what "
         "it gives back. Read each value with its meaning.")
#: How close in meaning (all-MiniLM cosine) her question must be to a job.
#: Measured: "what can I see in the sky tonight?" 0.64 to its job, under 0.40
#: to the bot's other jobs.
NUDGE_FIT = 0.45


def field_words(spec: dict, source_id: str) -> dict[str, str]:
    """``{row key: "Label" or "Label (what its values mean)"}`` for a source."""
    src = next((s for s in spec.get("sources") or [] if s.get("id") == source_id), {})
    labels = src.get("labels") or {}
    meanings = src.get("meanings") or {}
    out = {}
    for key in (src.get("fields") or {}):
        label = str(labels.get(key) or key.replace("_", " ").capitalize())
        means = str(meanings.get(key) or "")
        out[key] = f"{label} ({means})" if means else label
    return out


def _cap_names(spec: dict) -> list[str]:
    words = spec.get("cap_words") or {}
    out = []
    for cap in spec.get("caps") or []:
        text = str(words.get(cap) or "")
        out.append(text.split(": ", 1)[0] if text else
                   cap.removeprefix("cap-").replace("-", " ").capitalize())
    return out


def tool_description(spec: dict, action: dict) -> str:
    """One tool's words: the job, what it gives back, where it comes from."""
    title = str(spec.get("title") or spec.get("id") or "this app")
    jobs = "; ".join(_cap_names(spec))
    src = next((s for s in spec.get("sources") or [] if s.get("id") == action.get("source")),
               {})
    gives = "; ".join(f"{k}: {v}" for k, v in field_words(spec, src.get("id")).items())
    text = f"{title}. Does the job: {jobs}." if jobs else f"{title}."
    if gives:
        text += f" Gives a list of items, each with {gives}."
    else:
        text += " Gives a list of items."
    if src.get("name"):
        text += f" From {src['name']}."
    if "q" in (action.get("input") or {}):
        text += " Give q, one or two words to look for."
    return text[:DESCRIPTION_MAX]


# ── the proven apps and their tools ─────────────────────────────────────────

def proven_jobs() -> list[dict]:
    """``[{"cap", "job", "app", "tools": [names], "gives": {key: words}}]`` for
    every capability the report calls working that has an assembled app."""
    try:
        from puffpai.assemble import api, report
        rows = report.by_cap()
        specs = {s["id"]: s for s in api.all_specs()}
    except Exception as exc:  # noqa: BLE001 - nothing proven is nothing named
        logger.debug("apps note: %s", type(exc).__name__)
        return []
    out = []
    for cap, row in rows.items():
        spec = specs.get(str(row.get("app_id") or ""))
        if row.get("state") != report.WORKING or not spec:
            continue
        checks = [c for c in spec.get("checks") or [] if c.get("cap") == cap]
        actions = [c.get("action") for c in checks] or \
            [a.get("id") for a in spec.get("actions") or []]
        tools = [api.tool_name(spec["id"], a) for a in actions if a]
        first = next((a for a in spec.get("actions") or [] if a.get("id") == actions[0]), {}) \
            if actions else {}
        out.append({"cap": cap, "job": str(row.get("name") or cap), "app": spec["id"],
                    "tools": list(dict.fromkeys(tools)),
                    "gives": field_words(spec, first.get("source") or "")})
    return out


def _offered(jobs: list[dict], tools: Optional[list]) -> list[dict]:
    if tools is None:
        return jobs
    names = {getattr(t, "name", "") for t in tools}
    out = []
    for job in jobs:
        have = [n for n in job["tools"] if n in names]
        if have:
            out.append({**job, "tools": have})
    return out


def block(tools: Optional[list] = None) -> str:
    """The "Your apps" system note, or "" with no proven app on offer."""
    jobs = _offered(proven_jobs(), tools)
    if not jobs:
        return ""
    lines = []
    for job in jobs:
        gives = "; ".join(job["gives"].values())
        lines.append(f"- {job['job']}: call {job['tools'][0]}"
                     + (f" (gives: {gives})" if gives else ""))
    return BLOCK_HEAD + "\n".join(lines)


def tools_for_cap(cap_id: Any) -> list[str]:
    """The tool names of a proven capability (for the capabilities map)."""
    return next((j["tools"] for j in proven_jobs() if j["cap"] == str(cap_id or "")), [])


def nudge(question: Any, tools: Optional[list] = None, *, fit: Any = None) -> str:
    """One line pointing the brain at the proven job her question is about,
    or "" when it is about none of them (or the reading helper is missing)."""
    text = " ".join(str(question or "").split())[:400]
    jobs = _offered(proven_jobs(), tools)
    if not text or not jobs:
        return ""
    from puffpai.assemble import accept
    fit = fit or accept.meaning_fit
    best, best_sim = None, NUDGE_FIT
    for job in jobs:
        words = job["job"]
        try:
            from puffpai.finding import capabilities
            cap = capabilities.get(job["cap"]) or {}
            words = accept.cap_text(cap) or words
        except Exception:  # noqa: BLE001
            pass
        sim = fit(text, words)
        if sim is not None and sim >= best_sim:
            best, best_sim = job, sim
    if best is None:
        return ""
    return NUDGE.format(job=best["job"], tool=best["tools"][0])


__all__ = ["BLOCK_HEAD", "NUDGE_FIT", "block", "field_words", "nudge", "proven_jobs",
           "tool_description", "tools_for_cap"]
