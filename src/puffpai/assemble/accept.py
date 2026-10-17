"""Prove each capability of an assembled app with a real ask.

For every check in the spec, two halves, and both must pass:

1. **The action works, for this job.** The check must first fit its
   capability (:func:`fit_reason`): the view the capability's name asks for
   (``Show pictures ...`` needs a gallery, ``... positions`` a map) must show
   this answer, and its ask must be about the capability in meaning
   (all-MiniLM, when the reading helper is on this computer). Then the action
   is called (:func:`puffpai.assemble.api.call_action`) with the check's
   input, and the answer must hold at least ``min_items`` rows with every
   ``fields`` entry filled, and give each view that shows it what that view
   needs (:func:`view_problem`): a gallery a picture address on each of its
   first rows (a picture that does not load shows a "No picture" tile and
   never fails the job); a forecast a time and at least one named weather
   reading; a map a real position; a player a stream that plays, reached
   through the Bridge under the source's own switch (a ``HEAD``, or a small
   ``GET`` when the service does not answer ``HEAD``; a stream list must
   start with ``#EXTM3U``); any other
   view more than a name. A name alone never proves anything.
2. **The brain uses it.** The check's ask goes through the chat's own tool
   loop (:func:`puffpai.brain_loop.run`) with only this app's tools on offer;
   it passes only when the check's own action ran and answered with rows.

Anything else is ``not yet`` with the plain reason.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import re
from typing import Any, Callable, Optional
from urllib.parse import parse_qs, urlsplit

from puffpai.logging_config import get_logger

logger = get_logger(__name__)

REACH_TIMEOUT = 8.0
ASK_STEPS = 4
SYSTEM_NOTE = ("Answer the owner's question with this app's tools. Call the tool that "
               "fits, then answer in one or two plain sentences.")
_REACH_FIELDS = ("image", "stream")
#: How close in meaning (all-MiniLM cosine) a check's ask must be to its
#: capability. Measured on a real build: right asks 0.64 to 0.87, an ask
#: about another job of the same bot 0.11 to 0.39.
ASK_FIT = 0.45
#: Rows looked at per gallery (each must name a picture).
PICTURES_CHECKED = 3
_SLOTS = frozenset({"title", "image", "when", "text", "lat", "lon", "stream", "link"})
_WEATHER_RE = re.compile(r"(temp|cloud|rain|precip|wind|humid|snow|seeing|transparen|"
                         r"visib|pressure|\buv\b|dew|gust|storm|sky|weather|frost)", re.I)
_YOUTUBE_RE = re.compile(r"^https://(?:www\.|m\.)?(?:youtube\.com/(?:watch|embed|live)|"
                         r"youtube-nocookie\.com/embed|youtu\.be/)", re.I)


def _filled(value: Any) -> bool:
    return value not in (None, "", [], {})


# ── does the check fit its capability ───────────────────────────────────────

def meaning_fit(a: str, b: str) -> Optional[float]:
    """Cosine of two texts (all-MiniLM), or None without the reading helper."""
    if not str(a or "").strip() or not str(b or "").strip():
        return None
    try:
        from puffpai.catalogs import meaning
        vecs = meaning.embed([str(a), str(b)])
    except Exception as exc:  # noqa: BLE001 - no helper, no measure
        logger.info("assemble proof: no meaning check (%s)", type(exc).__name__)
        return None
    return float(vecs[0] @ vecs[1])


def cap_text(cap: dict) -> str:
    return " ".join(f"{cap.get('name') or ''}: {cap.get('does') or ''}".split()).strip(": ")


def cap_of(spec: dict, cap_id: str) -> dict:
    """The capability's words: from the spec, else the bot's list, else the plan."""
    words = (spec.get("cap_words") or {}).get(cap_id)
    if isinstance(words, str) and words:
        name, _, does = words.partition(": ")
        return {"id": cap_id, "name": name, "does": does}
    try:
        from puffpai.finding import capabilities
        got = capabilities.get(cap_id)
        if got:
            return got
        from puffpai.assemble import plan
        row = next((r for r in plan.load_plan()["capabilities"] if r.get("cap") == cap_id),
                   None)
        if row:
            return {"id": cap_id, "name": row.get("name") or "", "does": row.get("does") or ""}
    except Exception:  # noqa: BLE001 - unknown words: only the data checks apply
        pass
    return {"id": cap_id, "name": "", "does": ""}


#: What a part's own words must reach to do a job (all-MiniLM cosine of the
#: source's name and description against the job's words). Measured on a
#: real build: the right source 0.31 to 0.73; a photo service against
#: "satellite positions" 0.29.
SOURCE_FIT = 0.30
#: A part that fits another of the bot's jobs better than this one by more
#: than this does not do this one (the stargazing forecast fits "night sky"
#: 0.46 and "space weather" 0.43: it is the night sky's).
SOURCE_TIE = 0.02
NOTHING_DOES = "found nothing that does this"


def bot_caps() -> list[dict]:
    """All of the bot's jobs: its kept capabilities, else the plan's."""
    try:
        from puffpai.finding import capabilities
        caps = capabilities.load()
        if caps:
            return caps
        from puffpai.assemble import plan
        return [{"id": r.get("cap"), "name": r.get("name") or "", "does": r.get("does") or ""}
                for r in plan.load_plan()["capabilities"] if r.get("cap")]
    except Exception:  # noqa: BLE001
        return []


def source_words(src: dict) -> str:
    """What a source says it is: its name and its description."""
    return " ".join(f"{src.get('name') or ''}. {src.get('about') or ''}".split()).strip(" .")


def source_fit(cap: dict, words: str,
               fit: Optional[Callable[[str, str], Optional[float]]] = None) -> Optional[float]:
    """How close a part's words are to a job, or None (unmeasured)."""
    if not str(words or "").strip() or not cap_text(cap):
        return None
    return (fit or meaning_fit)(words, cap_text(cap))


def source_reason(cap: dict, words: str, others: Optional[list[dict]] = None,
                  fit: Optional[Callable[[str, str], Optional[float]]] = None) -> str:
    """"" when a part with these words does this job, else
    :data:`NOTHING_DOES`: it must reach SOURCE_FIT, and no other job of the
    bot may fit it better by more than SOURCE_TIE. Unmeasured (no helper, no
    words) is not a reason."""
    fit = fit or meaning_fit
    if not str(words or "").strip() or not cap_text(cap):
        return ""
    sim = fit(words, cap_text(cap))
    if sim is None:
        return ""
    if sim < SOURCE_FIT:
        return NOTHING_DOES
    for other in others if others is not None else bot_caps():
        if other.get("id") == cap.get("id") or not cap_text(other):
            continue
        got = fit(words, cap_text(other))
        if got is not None and got > sim + SOURCE_TIE:
            return NOTHING_DOES
    return ""


def fit_reason(cap: dict, ask: str, shown: list[str],
               fit: Optional[Callable[[str, str], Optional[float]]] = None) -> str:
    """"" when the check fits its capability, else why not."""
    from puffpai.assemble import spec as spec_mod
    for view in spec_mod.cap_views(cap):
        if view not in shown:
            return f"this job needs {spec_mod.VIEW_NEEDS[view]}, and its data has none"
    sim = (fit or meaning_fit)(ask, cap_text(cap))
    if sim is not None and sim < ASK_FIT:
        return "its test question is not about this job"
    return ""


# ── does the answer give the view what it needs ─────────────────────────────

def reachable(door: str, url: str, *, requester: Optional[Callable] = None) -> tuple[bool, str]:
    """Can this picture or stream address be reached through the Bridge?"""
    from puffpai.security import egress_doors
    if not door:
        return False, "it has no Bridge switch yet"
    if not str(url).startswith("https://"):
        return False, "its address is not https"
    send = requester or egress_doors.egress_request
    is_list = ".m3u8" in str(url).lower()
    try:
        try:
            resp = None if is_list else send(door, "HEAD", url, timeout=REACH_TIMEOUT)
        except egress_doors.EgressBlocked:
            resp = None       # a pictures-only site takes a plain GET only: asked below
        if resp is None or int(getattr(resp, "status_code", 0) or 0) in (403, 405, 501):
            resp = send(door, "GET", url, timeout=REACH_TIMEOUT, stream=True)
    except egress_doors.EgressBlocked:
        # Plain words only: the Bridge's own reason names its internals.
        return False, f"the Bridge does not let it reach {_host(url)}"
    except Exception as exc:  # noqa: BLE001
        return False, f"{_host(url)} did not answer ({type(exc).__name__})"
    try:
        code = int(getattr(resp, "status_code", 0) or 0)
        if code >= 400 or code == 0:
            return False, f"{_host(url)} answered {code}"
        if is_list:
            head = b""
            it = getattr(resp, "iter_content", None)
            for chunk in (it(1024) if callable(it) else [getattr(resp, "content", b"")]):
                head = bytes(chunk or b"")
                break
            if not head.lstrip(b"\xef\xbb\xbf").lstrip().startswith(b"#EXTM3U"):
                return False, "the stream address is not a stream list"
        return True, ""
    finally:
        close = getattr(resp, "close", None)
        if callable(close):
            close()


def _host(url: str) -> str:
    return urlsplit(str(url)).hostname or "that site"


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def outside_url(value: Any) -> str:
    """A row's address as it is outside: the app server hands streams out as
    ``/media?url=<address>`` (its own Bridge proxy)."""
    text = str(value or "") if isinstance(value, str) else ""
    if text.startswith("/media?"):
        return (parse_qs(urlsplit(text).query).get("url") or [""])[0]
    return text


def view_problem(view: str, rows: list, *, door: str = "", labels: Optional[dict] = None,
                 reach: Optional[Callable[[str, str], tuple[bool, str]]] = None) -> str:
    """"" when the rows give *view* what it needs, else the plain reason."""
    reach = reach or (lambda d, u: reachable(d, u))
    dicts = [r for r in rows or [] if isinstance(r, dict)]
    first = dicts[0] if dicts else {}
    labels = labels or {}
    if view == "gallery":
        # Rows with pictures are what a gallery needs. A picture that does
        # not load shows a "No picture" tile; it never fails a job whose data
        # loads.
        pics = [outside_url(r.get("image")) for r in dicts[:PICTURES_CHECKED]]
        if not pics or not all(pics):
            return "it has no pictures to show"
    elif view == "forecast":
        if not _filled(first.get("when")):
            return "its forecast has no times"
        if not any(k not in _SLOTS and _filled(v) and
                   _WEATHER_RE.search(f"{k} {labels.get(k) or ''}") for k, v in first.items()):
            return "its forecast has no weather readings"
    elif view == "map":
        lat, lon = _number(first.get("lat")), _number(first.get("lon"))
        if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
            return "it has no positions to put on a map"
    elif view == "player":
        url = outside_url(first.get("stream"))
        if not url:
            return "it has nothing to play"
        if not _YOUTUBE_RE.match(url):
            ok, why = reach(door, url)
            if not ok:
                return f"its stream: {why}"
    elif view not in ("settings", "form"):
        if sum(1 for v in first.values() if _filled(v)) < 2:
            return "it shows only a name, nothing more"
    return ""


def check_result(result: Any, expect: dict, *, door: str = "",
                 reach: Optional[Callable[[str, str], tuple[bool, str]]] = None
                 ) -> tuple[bool, str, int]:
    """``(ok, reason, rows)`` for one action answer against its ``expect``."""
    if not isinstance(result, dict) or not result.get("ok"):
        reason = (result or {}).get("reason") if isinstance(result, dict) else ""
        return False, str(reason or "the action did not answer"), 0
    rows = result.get("rows")
    rows = rows if isinstance(rows, list) else []
    need = int(expect.get("min_items", 1) or 0)
    if len(rows) < need:
        return False, f"it answered {len(rows)} item{'s' if len(rows) != 1 else ''}, " \
                      f"not {need}", len(rows)
    fields = [str(f) for f in expect.get("fields") or []]
    if rows:
        first = rows[0] if isinstance(rows[0], dict) else {}
        empty = [f for f in fields if not _filled(first.get(f))]
        if empty:
            return False, f"its {empty[0]} was empty", len(rows)
        reach = reach or (lambda d, u: reachable(d, u))
        for f in fields:
            if f == "image":
                continue      # a picture that does not load is a tile, never a failed job
            if f in _REACH_FIELDS and isinstance(first.get(f), str):
                url = outside_url(first[f])
                if f == "stream" and _YOUTUBE_RE.match(url):
                    continue
                ok, why = reach(door, url)
                if not ok:
                    return False, f"its {'picture' if f == 'image' else 'stream'}: {why}", \
                        len(rows)
    return True, "", len(rows)


# ── does the brain use it ───────────────────────────────────────────────────

def _default_backend() -> Any:
    from puffpai.config import get_config
    from puffpai.inference.ollama_backend import OllamaBackend
    cfg = get_config()
    return OllamaBackend(url=cfg.ollama.url, model=cfg.ollama.model, num_ctx=8192,
                         num_predict=512, timeout=180, think=False)


def ask_through_brain(app_id: str, spec: dict, check: dict, *,
                      backend: Any = None) -> tuple[bool, str, list[str]]:
    """``(ok, reason, tools used)``: the ask, through the chat's tool loop with
    only this app's tools; ok when the check's action ran and gave rows."""
    from puffpai import brain_loop
    from puffpai.assemble import api
    api.register_tools()
    tools = api.app_tools(app_id, spec)
    want = api.tool_name(app_id, check.get("action"))
    if not any(t.name == want for t in tools):
        return False, "the app has no tool for this", []
    ctx: dict = {"assemble_app": app_id, "assemble_seen": []}
    messages = [{"role": "system", "content": SYSTEM_NOTE},
                {"role": "user", "content": str(check.get("ask") or "")}]
    try:
        got = brain_loop.run(messages, ctx=ctx, backend=backend or _default_backend(),
                             tools=tools, max_steps=ASK_STEPS)
    except Exception as exc:  # noqa: BLE001 - no brain is a reason, not a crash
        return False, f"the brain did not answer ({type(exc).__name__})", []
    used = [str(u) for u in got.get("used") or []]
    if want not in used:
        other = next((u for u in used if u != want), "")
        return False, (f"the brain used {other} instead" if other else
                       "the brain answered without using the app"), used
    rows = max((n for name, n in ctx["assemble_seen"] if name == want), default=0)
    if rows < 1:
        return False, "the brain used the app, and it gave nothing back", used
    return True, "", used


def prove(app_id: str, spec: dict, *, backend: Any = None,
          reach: Optional[Callable[[str, str], tuple[bool, str]]] = None,
          call: Optional[Callable[[str, str, dict], dict]] = None,
          fit: Optional[Callable[[str, str], Optional[float]]] = None,
          others: Optional[list[dict]] = None) -> dict[str, dict]:
    """``{cap id: {"ok", "reason", "checks": [...]}}`` for every check.
    *fit* measures meaning and *others* are the bot's jobs (tests pass
    their own)."""
    from puffpai.assemble import api
    others = bot_caps() if others is None else others
    call = call or api.call_action
    doors = {s.get("id"): str(s.get("door") or "") for s in spec.get("sources") or []}
    labels = {s.get("id"): s.get("labels") or {} for s in spec.get("sources") or []}
    actions = {a.get("id"): a for a in spec.get("actions") or []}
    out: dict[str, dict] = {}
    for check in spec.get("checks") or []:
        cap = str(check.get("cap") or "")
        action = actions.get(check.get("action")) or {}
        sid = action.get("source")
        shown = [str(v.get("type")) for v in spec.get("views") or []
                 if v.get("source") == sid]
        row = {"ask": str(check.get("ask") or ""), "action": str(check.get("action") or ""),
               "ok": False, "rows": 0, "tool_ran": False, "reason": ""}
        src = next((s for s in spec.get("sources") or [] if s.get("id") == sid), {})
        why = fit_reason(cap_of(spec, cap), row["ask"], shown, fit) or \
            source_reason(cap_of(spec, cap), source_words(src), others, fit)
        ok = not why
        if ok:
            try:
                result = call(app_id, row["action"], dict(check.get("input") or {}))
            except Exception as exc:  # noqa: BLE001
                result = {"ok": False, "reason": f"the action failed ({type(exc).__name__})"}
            ok, why, n = check_result(result, check.get("expect") or {},
                                      door=doors.get(sid, ""), reach=reach)
            row["rows"] = n
            if isinstance(result, dict) and result.get("fix"):
                row["fix"] = result["fix"]
            for view in shown if ok else ():
                why = view_problem(view, result.get("rows") or [], door=doors.get(sid, ""),
                                   labels=labels.get(sid), reach=reach)
                if why:
                    ok = False
                    break
        if ok:
            ok, why, used = ask_through_brain(app_id, spec, check, backend=backend)
            row["tool_ran"] = api.tool_name(app_id, row["action"]) in used
        row["ok"], row["reason"] = ok, why
        prev = out.get(cap)
        checks = (prev or {}).get("checks", []) + [row]
        out[cap] = {"ok": all(c["ok"] for c in checks), "checks": checks,
                    "reason": next((c["reason"] for c in checks if not c["ok"]), "")}
    return out


__all__ = ["ASK_FIT", "ask_through_brain", "cap_of", "cap_text", "check_result",
           "fit_reason", "meaning_fit", "outside_url", "prove", "reachable", "view_problem"]
