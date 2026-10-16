"""Acceptance harness: needs, where to look, relevance, integration.

Run from the repository root with ``PYTHONPATH=src``. Every goal in the goals
file goes through the same engine the wizard uses:

1. ``puffpai.wizard.needs.run`` with the local brain (Ollama at
   ``PUFFPAI_OLLAMA_URL``, default ``http://127.0.0.1:11435``);
2. ``puffpai.wizard.find_parts.plan_items`` on this computer only (``--dry``:
   nothing is dialled, every outside request is refused and listed in the
   report as refused) or online after the owner's yes (``--live``);
3. with ``--integrate``, the wizard is walked over the HTTP API of a running
   backend at ``--base-url``: the best addable line per job is ticked, as an
   owner would, "Looks good" is pressed and the build is followed to its end.

Every outgoing request the engine makes is captured (URL, method, header names
only, body) by wrapping ``source_fetch``'s transport and ``urllib``'s opener.

``--discovery`` runs the discovery bar: a keyless
live run on the 12 discovery goals, and per goal: at least 2 addable finds on
the plan, at least 1 pre-ticked where trust allows, searching within 90 s, and
for a goal that fails, every raw find with the reason it was left out. It writes
``F_discovery_<run>.json`` and ``.md`` into ``--out``.

Outputs (other modes): ``report.json`` and ``report.md`` in ``--out``.

Nothing here is tuned to a goal: the goals file is only what is measured.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sys
import threading
import time
from typing import Any, Callable, Optional
from urllib.parse import unquote

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_GOALS = os.path.join(ROOT, "tests", "fixtures", "tuning_goals.json")
DISCOVERY_GOALS = os.path.join(ROOT, "tests", "fixtures", "r3_discovery_goals.json")
DEFAULT_MODEL = "qwen2.5:7b-instruct"
RECALL_COSINE = 0.45
SEARCH_LIMIT_S = 90.0
ADDABLE_KINDS = ("code_project", "data_import", "cli_tool", "picture_set")
TOKEN_ENV = ("GITHUB_TOKEN", "GH_TOKEN")
#: Requests built from her words: searches (a closer look names the find itself).
SEARCH_URL = re.compile(r"/search\b|[?&](?:q|query|text|search|srsearch)=", re.I)
#: A search site's own qualifiers ("in:name,description", "pushed:>=2024-10-03").
_QUALIFIER = re.compile(r"(?<![a-z0-9])(?:in|language|archived|fork|pushed|created|stars|topic|"
                        r"license|is|size|sort|user|org):[^\s&+/?]*")


DRY_REFUSED = "a --dry run dials nothing outside this computer"


class Refused(SystemExit):
    """The harness will not start (for example a GitHub key is present with --keyless)."""


# -- capture ------------------------------------------------------------------------
class Capture:
    """Every outgoing request while installed: URL, method, header names, body."""

    def __init__(self, *, refuse: bool = False) -> None:
        self.rows: list[dict] = []
        self._lock = threading.Lock()
        self._undo: list[Callable[[], None]] = []
        #: ``--dry``: every outside request is recorded and refused, so a dry
        #: run dials nothing (not even the closer looks at finds already on
        #: this computer).
        self.refuse = refuse

    def record(self, url: str, method: str = "GET", headers: Any = None, body: Any = None,
               via: str = "") -> None:
        names = sorted(str(k).lower() for k in (headers or {}).keys()) \
            if isinstance(headers, dict) else []
        if isinstance(body, (bytes, bytearray)):
            body = bytes(body[:2000]).decode("utf-8", "replace")
        with self._lock:
            self.rows.append({"url": str(url), "method": method, "headers": names,
                              "body": body, "via": via, "at": round(time.time(), 3),
                              **({"refused": True} if self.refuse else {})})

    def install(self) -> "Capture":
        from puffpai import source_fetch
        real = source_fetch._default_transport

        def transport(url, headers, timeout):
            self.record(url, "GET", headers, None, via="source_fetch")
            if self.refuse:
                raise OSError(DRY_REFUSED)
            return real(url, headers, timeout)
        source_fetch._default_transport = transport
        self._undo.append(lambda: setattr(source_fetch, "_default_transport", real))

        import urllib.request as ur
        real_open = ur.urlopen

        def urlopen(req, *a, **kw):
            url = getattr(req, "full_url", req)
            host = re.sub(r"^https?://([^/:]+).*$", r"\1", str(url))
            if host not in ("127.0.0.1", "localhost", "::1"):
                self.record(str(url), getattr(req, "get_method", lambda: "GET")(),
                            dict(getattr(req, "headers", {}) or {}), getattr(req, "data", None),
                            via="urllib")
                if self.refuse:
                    raise OSError(DRY_REFUSED)
            return real_open(req, *a, **kw)
        ur.urlopen = urlopen
        self._undo.append(lambda: setattr(ur, "urlopen", real_open))
        return self

    def uninstall(self) -> None:
        while self._undo:
            self._undo.pop()()

    def take(self) -> list[dict]:
        with self._lock:
            rows, self.rows = self.rows, []
        return rows


# -- checks -------------------------------------------------------------------------
def github_key_present() -> Optional[str]:
    """Why this run is not keyless, or None."""
    for name in TOKEN_ENV:
        if os.environ.get(name):
            return f"{name} is set"
    try:
        from puffpai.security import keys
        index = keys._read_index()
        if any("github" in str(k).lower() for k in (index or {})):
            return "a github key is in the vault"
    except Exception:  # noqa: BLE001 - no vault is no key
        pass
    return None


def load_goals(path: str, only: Optional[list[str]] = None) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    goals = [g for g in data.get("goals") or [] if isinstance(g, dict) and g.get("text")]
    if only:
        goals = [g for g in goals if g.get("id") in only]
    return goals


def recall(needs: dict, gold: list[str]) -> dict:
    """Gold jobs covered by a job name (cosine >= 0.45 with the reading helper)."""
    from puffpai.catalogs import meaning
    jobs = [f"{needs.get('kind') or ''} {j.get('name') or ''}".strip()
            for j in needs.get("jobs") or []]
    if not gold:
        return {"recall": None, "matched": []}
    if not jobs:
        return {"recall": 0.0, "matched": [None] * len(gold)}
    if meaning.helper_state() != "ready":
        return {"recall": None, "matched": [], "why": "reading helper missing"}
    kind = str(needs.get("kind") or "")
    sims = meaning.embed([f"{kind} {g}".strip() for g in gold]) @ meaning.embed(jobs).T
    matched = []
    for i in range(len(gold)):
        k = int(sims[i].argmax())
        matched.append({"gold": gold[i], "job": jobs[k], "cos": round(float(sims[i, k]), 3)}
                       if float(sims[i, k]) >= RECALL_COSINE else None)
    hit = sum(1 for m in matched if m)
    return {"recall": round(hit / len(gold), 3), "matched": matched}


def personal_leaks(goal: dict, needs: dict, captured: list[dict]) -> list[str]:
    """Personal words of the goal that appear in an ``out`` phrase or a captured URL."""
    words: set[str] = set()
    for p in goal.get("personal") or []:
        for w in re.findall(r"[a-z0-9]+", str(p).lower()):
            if len(w) > 1:
                words.add(w)
    out = []
    for job in needs.get("jobs") or []:
        for part in job.get("parts") or []:
            for s in part.get("search") or []:
                if isinstance(s, dict) and s.get("state") == "out":
                    bad = set(str(s.get("phrase") or "").lower().split()) & words
                    out += [f"phrase: {s.get('phrase')} ({', '.join(sorted(bad))})"] if bad else []
    for row in captured:
        url = str(row.get("url") or "")
        if not SEARCH_URL.search(url):
            continue   # a closer look at a find names that find, not her words
        # The search site's own syntax ("in:name,description", "language:HTML")
        # is not her words: "in" of "mother-in-law" matched "in:name".
        url_words = set(re.findall(r"[a-z0-9]+", _QUALIFIER.sub(" ", unquote(url.lower()))))
        bad = url_words & words
        if bad:
            out.append(f"request: {row['url'][:200]} ({', '.join(sorted(bad))})")
    return out


def _requests_words(captured: list[dict]) -> str:
    """"115 requests", or for a dry run "0 requests (3 refused)"."""
    refused = sum(1 for row in captured if row.get("refused"))
    if not refused:
        return f"{len(captured)} requests"
    return f"{len(captured) - refused} requests ({refused} refused)"


def addable(items: list[dict]) -> list[dict]:
    """Plan lines that are finds BuildBot can add (not its own lines)."""
    return [i for i in items if (i.get("apply") or {}).get("kind") in ADDABLE_KINDS
            and ":buildbot:" not in str(i.get("id"))]


def bar_for(result: dict) -> dict:
    """The discovery bar for one goal (directive section 2)."""
    from puffpai.wizard import find_parts
    finds = addable(result["plan"]["items"])
    from puffpai.catalogs import meaning
    # "Pre-ticked where trust allows": a find trust allows to pre-tick that is
    # also a strong fit (moderate fits are shown unticked, directive section 2).
    # A strong one the brain said "no" to still counts, so a judge that says
    # "no" to every strong find fails the goal.
    pretickable = []
    held_by_trust = []
    for i in finds:
        rec = find_parts.found_for(i["id"]) or {}
        fit = (rec.get("meaning") or {}).get("fit")
        strong = fit is None or fit >= meaning.TICK_FIT
        trust = rec.get("trust") or {}
        if trust.get("pretick_ok") and strong:
            pretickable.append(i["id"])
        elif strong:
            held_by_trust.append({"id": i["id"], "fit": fit, "path": trust.get("path") or "",
                                  "reasons": [str(x) for x in trust.get("reasons") or []],
                                  "why": _never_ticked_why(i, trust)})
    ticked = [i["id"] for i in finds if i.get("default_on")]
    checks = {
        "two_finds": len(finds) >= 2,
        # The directive's rule: a tick where trust allows one (passes untried
        # when no strong find trust allows). What the owner sees is
        # ``one_preticked`` below, never this (it can say "true" for a plan
        # with no ticked line).
        "pretick_where_trust_allows": bool(ticked) or not pretickable,
        "within_90s": (result["timings"].get("search_s") or 0.0) <= SEARCH_LIMIT_S,
        "no_personal_word_sent": not result["personal_leaks"],
    }
    # "Where trust allows": with no strong find trust allows to pre-tick, the
    # check passes without being tried. Said so, never shown as a plain "yes".
    pretick = "ticked" if ticked else "missed" if pretickable else "not_tried"
    # The top unticked find of a job with nothing ticked says "Best match for
    # this job."; it never ticks anything.
    best = [i["id"] for i in result["plan"]["items"] if i.get("best_match")]
    return {"pass": all(checks.values()), "checks": checks, "finds": len(finds),
            "one_preticked": bool(ticked),
            "preticked": ticked, "pretickable": pretickable, "pretick": pretick,
            "held_by_trust": held_by_trust, "best_match": best}


def _never_ticked_why(item: dict, trust: dict) -> str:
    """Why trust keeps a strong find's tick off, in plain words."""
    if item.get("section") == "pictures" or str(item.get("id")).startswith("pictures:"):
        return "stock picture (never pre-ticked)"
    if trust.get("path") == "static_small":
        return "small app with few users (shown, never pre-ticked)"
    reasons = [str(x) for x in trust.get("reasons") or [] if x]
    return "trust: " + ", ".join(reasons) if reasons else "trust"


def pretick_words(bar: dict) -> str:
    """The pre-tick check in plain words for the report."""
    state = bar.get("pretick")
    if state == "not_tried":
        held = bar.get("held_by_trust") or []
        why: dict[str, int] = {}
        for row in held:
            key = row.get("why") or ", ".join(row["reasons"]) or row["path"] or "trust"
            why[key] = why.get(key, 0) + 1
        return ("pretick_where_trust_allows=not tried (no strong find trust allows to pre-tick; "
                f"{len(held)} strong finds kept unticked"
                + (": " + "; ".join(f"{k} x{v}" for k, v in sorted(why.items(),
                                                                  key=lambda kv: -kv[1]))
                   if why else "") + ")")
    allowed = (bar.get("checks") or {}).get("pretick_where_trust_allows")
    return f"pretick_where_trust_allows={'yes' if allowed else 'NO'}"


#: A code find "fits" for the failure proof from this fit (the note threshold).
PROOF_FIT = 0.45
PROOF_CODE_SOURCES = ("github", "codeberg", "gitlab", "npm", "pypi", "awesome_selfhosted")


def _open_licence(facts: dict) -> bool:
    from puffpai.catalogs import where_to_look
    for key in ("license_read", "license_reported"):
        value = str((facts or {}).get(key) or "").strip()
        if value and value.lower() not in ("none", "null", "noassertion", "other", "unknown"):
            try:
                if where_to_look.license_words(value) is not None:
                    return True
            except Exception:  # noqa: BLE001 - an unreadable licence is none
                pass
    return False


_CODE_HOSTS = {"github": "github.com", "codeberg": "codeberg.org", "gitlab": "gitlab.com"}
#: Why a fitting find read at its pin has no licence verdict: the build's share
#: of GitHub's keyless allowance was used up (not a product failure, but the
#: nothing-integrable proof cannot be given for it).
ALLOWANCE_REASON = "licence not checked: GitHub allowance used up"


def _address_of(f: dict) -> str:
    """``https://<host>/<owner>/<name>`` for a code-host find id, else ""."""
    source, _, rest = str(f.get("id") or "").partition(":")
    host = _CODE_HOSTS.get(source)
    return f"https://{host}/{rest}" if host and rest.count("/") == 1 else ""


def proof_reason(f: dict, on_plan: set) -> str:
    """Why one fitting code find is not an addable line on the plan (plain words)."""
    m = f.get("meaning") or {}
    trust = f.get("trust") or {}
    mode = str(f.get("mode") or "unknown")
    if f["id"] in on_plan:
        return "on the plan"
    if m.get("why_dropped") in ("page", "reads like orders"):
        return "a page or orders, not a tool"
    facts0 = f.get("facts") or {}
    if str(facts0.get("license_read") or "") == "LicenseRef-not-checked" \
            and mode in ("unknown", "static_web", "web_build", "cli_tool", "data_template"):
        # Read, of a kind that could be added,
        # but its licence could not be confirmed because the build's keyless
        # GitHub allowance was used up (a server app says so below instead).
        return ALLOWANCE_REASON
    unsaid = f.get("source") in ("codeberg", "gitlab")    # their search names no licence
    # GitHub's "NOASSERTION" (or "other") means a licence file it
    # cannot name: unread, that is "not looked at", never "no open licence".
    unsaid = unsaid or (f.get("source") == "github" and not facts0.get("license_read")
                        and str(facts0.get("license_reported") or "").strip().lower()
                        in ("noassertion", "other"))
    if f.get("source") in PROOF_CODE_SOURCES and not _open_licence(f.get("facts") or {}) \
            and mode in ("unknown", "static_web", "web_build", "cli_tool", "data_template") \
            and not (unsaid and mode == "unknown"):
        return "no open licence"
    if mode == "unknown":
        from puffpai.catalogs import meaning
        facts = f.get("facts") or {}
        # A find that was never read has no trust reasons yet, so the rule the
        # plan used to skip it (meaning.trust_hopeless: few stars, not on the
        # self-hosting list, not changed for over 24 months) is checked here
        # directly, with the find's own address for the list (otherwise such
        # finds are counted as "not looked at in time").
        rec = {"source": f.get("source"), "trust": trust, "url": _address_of(f),
               "repo": {"stars": facts.get("stars"), "pushed_at": facts.get("pushed_at")}}
        if meaning.trust_hopeless(rec):
            return "trust: few stars, not listed, not changed for over 24 months"
        return "not looked at in time"
    if not f.get("integrable"):
        return f"cannot be added in round 3 ({mode})"
    if not _open_licence(f.get("facts") or {}):
        return "no open licence"
    if not trust.get("floor_ok"):
        return "trust: " + (", ".join(trust.get("reasons") or []) or "floor")
    if m.get("judge") == "no":
        return "the brain says it does not do this job"
    return str(m.get("why_dropped") or m.get("state") or "left out")


def unsearched_parts(r: dict) -> list[str]:
    """The ticked jobs' code parts that were never asked of a code site: no
    phrase allowed out, or the job was not reached in time (otherwise the
    report says "nothing integrable found" for a part GitHub was never asked
    about)."""
    late = {str(j) for j in (r.get("plan") or {}).get("not_reached") or []}
    out = []
    for job in (r.get("needs") or {}).get("jobs") or []:
        if not job.get("ticked", True):
            continue
        for part in job.get("parts") or []:
            if part.get("kind") not in ("code-app", "data-template"):
                continue
            sent = [s for s in part.get("search") or []
                    if (s.get("state") if isinstance(s, dict) else "out") == "out"]
            if not sent or str(job.get("id")) in late:
                out.append(f"{job.get('id')}:{part.get('kind')}")
    return out


def failure_proof(r: dict) -> dict:
    """For a goal that fails the bar: every fitting code find and why it is not
    addable. The failure is "nothing integrable exists" only when no fitting
    find with an open licence was left unread; otherwise it says "not proven"."""
    on_plan = {str((i.get("apply") or {}).get("ref") or "") for i in r["plan"].get("items") or []}
    rows = []
    for f in (r.get("evidence") or {}).get("finds") or []:
        if f.get("part") not in ("code-app", "data-template") \
                or f.get("source") not in PROOF_CODE_SOURCES:
            continue
        m = f.get("meaning") or {}
        fit = m.get("fit") if m.get("fit") is not None else m.get("blurb")
        if fit is None or fit < PROOF_FIT:
            continue
        rows.append({"id": f["id"], "fit": fit, "reason": proof_reason(f, on_plan)})
    rows.sort(key=lambda x: -x["fit"])
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["reason"]] = counts.get(row["reason"], 0) + 1
    unread = counts.get("not looked at in time", 0)
    allowance = counts.get(ALLOWANCE_REASON, 0)
    trust = sum(v for k, v in counts.items() if k.startswith("trust"))
    missed = unsearched_parts(r)
    if missed:
        verdict = (f"not proven: {len(missed)} job parts were never searched "
                   f"({', '.join(missed[:6])})")
    elif unread:
        verdict = (f"not proven: {unread} fitting finds with an open licence were not looked "
                   "at in time")
    elif allowance:
        verdict = (f"not proven: {allowance} fitting finds were read but their licences could "
                   "not be checked, because the GitHub allowance was used up (not a product "
                   "failure)")
    elif trust:
        verdict = (f"every fitting find was read or could never pass the trust floor; {trust} "
                   "fail the trust floor, the rest cannot be added (reasons "
                   "below)")
    else:
        verdict = "nothing integrable found: every fitting find is shown with its reason"
    out = {"verdict": verdict, "proven": not unread and not missed and not allowance,
           "trust_blocked": trust, "allowance_blocked": allowance,
           "counts": counts, "finds": rows, "unsearched_parts": missed}
    if not ((r.get("bar") or {}).get("checks") or {}).get("pretick_where_trust_allows", True):
        out["pretick"] = pretick_proof(r)
    return out


def pretick_proof(r: dict) -> list[dict]:
    """For a goal whose strong, trusted finds were all shown unticked: each one
    and the product rule that kept its tick off."""
    from puffpai.wizard import find_parts
    jobs = {str(j.get("id")): j for j in (r.get("needs") or {}).get("jobs") or []}
    by_id = {str(i.get("id")): i for i in (r.get("plan") or {}).get("items") or []}
    out = []
    for item_id in (r.get("bar") or {}).get("pretickable") or []:
        rec = find_parts.found_for(item_id) or {}
        m = rec.get("meaning") or {}
        job = jobs.get(str(m.get("job"))) or {}
        if m.get("judge") == "no":
            why = "the brain said it does not do this job"
        elif job.get("source") not in ("asked", "owner"):
            why = (f"filed under \"{job.get('name')}\", a job BuildBot added, not one she listed "
                   "(only her own jobs pre-tick)")
        elif not str(rec.get("what_it_is") or "").strip():
            why = "its author says nothing about what it is"
        elif m.get("judge") is None:
            why = "the brain had no time to check it (with a brain, a tick needs its yes)"
        else:
            why = "not ticked for another reason"
        out.append({"id": item_id, "fit": m.get("fit"), "job": job.get("name"),
                    "job_source": job.get("source"), "judge": m.get("judge"),
                    "on": bool((by_id.get(item_id) or {}).get("default_on")), "why": why})
    return out


# -- one goal ---------------------------------------------------------------------------
def run_goal(goal: dict, *, cfg: dict, online: bool, capture: Capture,
             say: Callable[[str], None], reuse: Optional[dict] = None) -> dict:
    from puffpai.wizard import find_parts, needs
    lines: list[str] = []
    capture.take()
    t0 = time.monotonic()
    if reuse is not None and reuse.get("goal_digest") == needs.goal_digest(goal["text"]):
        # The same jobs as an earlier run: its searches answer from the
        # 24-hour cache, so a re-measure costs no search allowance.
        got = json.loads(json.dumps(reuse))
        needs._GOALS[got["goal_digest"]] = (goal["text"], None)
        lines.append("(jobs reused from an earlier run)")
    else:
        got = needs.run(goal["text"], cfg=cfg, on_line=lines.append)
    needs_s = time.monotonic() - t0
    say(f"  needs: {len(got['jobs'])} jobs in {needs_s:.1f}s ({got['prompt']})")
    plan_lines: list[str] = []
    t1 = time.monotonic()
    plan = find_parts.plan_items(got, goal=goal["text"], online=online, allow_send=online,
                                 cfg=cfg, on_line=plan_lines.append)
    search_s = time.monotonic() - t1
    captured = capture.take()
    evidence = find_parts.last_evidence() or {}
    say(f"  plan: {len(plan['items'])} lines, {len(addable(plan['items']))} addable, "
        f"{search_s:.1f}s, {_requests_words(captured)}")
    items = []
    by_ref = {f["id"]: f for f in evidence.get("finds") or []}
    for item in plan["items"]:
        rec = find_parts.found_for(item["id"]) or by_ref.get(
            str((item.get("apply") or {}).get("ref") or "")) or {}
        items.append({"id": item["id"], "section": item.get("section"), "label": item.get("label"),
                      "detail": item.get("detail"), "on": bool(item.get("default_on")),
                      "need": item.get("need"), "apply": item.get("apply"),
                      "might_help": item.get("might_help"),
                      "best_match": bool(item.get("best_match")),
                      "meaning": rec.get("meaning"), "trust": rec.get("trust")})
    return {
        "id": goal.get("id"), "needs": got, "needs_lines": lines, "plan_lines": plan_lines,
        "plan": plan, "items": items, "evidence": evidence,
        "recall": recall(got, list(goal.get("gold") or [])),
        "personal_leaks": personal_leaks(goal, got, captured),
        "sends": plan.get("sends") or [], "captured": captured,
        "timings": {"needs_s": round(needs_s, 2), "search_s": round(search_s, 2),
                    **{k: v for k, v in (got.get("timings") or {}).items()}},
    }


# -- integration over the HTTP API -------------------------------------------------------
class Http:
    def __init__(self, base_url: str, key: str, opener=None) -> None:
        self.base, self.key = base_url.rstrip("/"), key
        self.opener = opener

    def call(self, method: str, path: str, body: Any = None, timeout: float = 600) -> tuple:
        import urllib.error
        import urllib.request
        data = None if body is None else json.dumps(body).encode("utf-8")
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"X-API-Key": self.key,
                                              "Content-Type": "application/json"})
        opener = self.opener or urllib.request.urlopen
        try:
            with opener(req, timeout=timeout) as fh:
                return fh.status, json.loads(fh.read() or b"{}")
        except urllib.error.HTTPError as exc:
            try:
                return exc.code, json.loads(exc.read() or b"{}")
            except ValueError:
                return exc.code, {}


def best_per_job(summary: dict) -> dict[str, bool]:
    """Tick the best addable line of every job, as an owner would."""
    rows: list[dict] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("id") and isinstance(node.get("apply"), dict):
                rows.append(node)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(summary)
    best: dict[str, dict] = {}
    for row in rows:
        if (row.get("apply") or {}).get("kind") not in ADDABLE_KINDS or not row.get("need"):
            continue
        cur = best.get(row["need"])
        if cur is None or (bool(row.get("default_on") or row.get("on")) and
                           not bool(cur.get("default_on") or cur.get("on"))):
            best[row["need"]] = row
    return {row["id"]: True for row in best.values()}


def integrate(goal: dict, http: Http, *, name: str, wait_s: float = 1500,
              sleep: Callable[[float], None] = time.sleep) -> dict:
    out: dict = {"steps": []}
    t0 = time.monotonic()

    def step(label: str, got: tuple) -> dict:
        out["steps"].append({"step": label, "status": got[0]})
        return got[1] if isinstance(got[1], dict) else {}
    step("first-look", http.call("POST", "/wizard/first-look", {"choose": True, "model": None}))
    step("goal", http.call("POST", "/wizard/apply",
                           {"skill": "purpose:goal", "answers": {"goal": goal["text"]}}))
    step("name", http.call("POST", "/wizard/apply",
                           {"skill": "purpose:identity", "answers": {"name": name, "age": ""}}))
    while time.monotonic() - t0 < wait_s:
        n = step("needs", http.call("GET", "/wizard/needs"))
        if n.get("done") or not n:
            break
        sleep(3)
    step("look", http.call("POST", "/wizard/needs", {"added": [], "allowed_words": [],
                                                      "look": "online", "get_helper": False}))
    while time.monotonic() - t0 < wait_s:
        n = step("parts", http.call("GET", "/wizard/needs"))
        if (n.get("phase") == "parts" and n.get("done")) or not n:
            break
        sleep(5)
    summary = step("summary", http.call("POST", "/wizard/summary", {}))
    changes = {"on": best_per_job(summary)}
    out["ticked"] = sorted(changes["on"])
    step("approve", http.call("POST", "/wizard/approve", {"changes": changes}))
    step("build", http.call("POST", "/wizard/build", {}))
    status: dict = {}
    while time.monotonic() - t0 < wait_s:
        status = step("build-status", http.call("GET", "/wizard/build"))
        if not status.get("running"):
            break
        sleep(10)
    out["build"] = {k: status.get(k) for k in ("running", "status", "lines", "done") if k in status}
    out["seconds"] = round(time.monotonic() - t0, 1)
    return out


# -- reports -------------------------------------------------------------------------------
def _job_line(j: dict) -> str:
    phrases = [f"{s['phrase']} [{s['state']}]" for p in j.get("parts") or []
               for s in p.get("search") or [] if isinstance(s, dict)]
    return (f"- {j['id']} {j['name']} ({j['source']}{', learn' if j.get('learn') else ''}; "
            f"parts: {', '.join(p['kind'] for p in j.get('parts') or [])}) "
            f"searches: {', '.join(phrases[:8]) or 'none'}")


def goal_md(r: dict) -> list[str]:
    out = [f"## {r['id']}", ""]
    rc = r["recall"]
    out.append(f"Needs: {len(r['needs']['jobs'])} jobs ({r['needs']['prompt']}), "
               f"{r['timings']['needs_s']} s; recall vs gold: {rc.get('recall')}; "
               f"searching {r['timings']['search_s']} s; {_requests_words(r['captured'])}."
               + (f" Stages (s from the start of searching): "
                  + ", ".join(f"{k} {v}" for k, v in
                              ((r.get('evidence') or {}).get('timings') or {}).items())
                  if (r.get('evidence') or {}).get('timings') else ""))
    if r.get("bar"):
        b = r["bar"]
        words = [pretick_words(b) if k == "pretick_where_trust_allows"
                 else f"{k}={'yes' if v else 'NO'}" for k, v in b["checks"].items()]
        label = "PASS" if b["pass"] else "FAIL"
        if b["pass"] and b.get("pretick") == "not_tried":
            label = "PASS (pre-tick not tried)"
        out.append(f"Discovery bar: **{label}** " + ", ".join(words)
                   + f"; addable finds {b['finds']}, ticked on the plan she sees "
                   + f"{len(b['preticked'])}"
                   + f", marked best match {len(b.get('best_match') or [])}.")
    out += ["", "Jobs:"] + [_job_line(j) for j in r["needs"]["jobs"]] + [""]
    out.append("Plan lines:")
    for i in r["items"]:
        m = i.get("meaning") or {}
        out.append(f"- [{'x' if i['on'] else ' '}] {i['section']} | {i['label']} | need "
                   f"{i.get('need')} | fit {m.get('fit')} | {i.get('detail') or ''}"[:400])
    if not r["items"]:
        out.append("- (none)")
    for jid, notes in (r["plan"].get("notes") or {}).items():
        for n in notes:
            out.append(f"- note {jid}: {n}")
    if r["plan"].get("not_reached"):
        out.append(f"- not reached: {', '.join(r['plan']['not_reached'])}")
    for s in r["plan"].get("similar") or []:
        out.append(f"- similar projects also have: {s['name']} (seen in {s['seen_in']})")
    out.append(f"- left out: {r['plan'].get('left_out')}")
    out += ["", "Sent: " + (", ".join(f"{s['phrase']} ({s['source']})" for s in r["sends"])
                            or "nothing")]
    if r["personal_leaks"]:
        out.append("PERSONAL WORDS SENT: " + "; ".join(r["personal_leaks"]))
    proof = (r.get("bar") or {}).get("proof")
    if proof:
        out += ["", f"Why it failed: {proof['verdict']}. Fitting code finds by reason: "
                + (", ".join(f"{k} {v}" for k, v in sorted(proof["counts"].items(),
                                                           key=lambda kv: -kv[1])) or "none")
                + "."]
        for row in proof["finds"][:40]:
            out.append(f"- {row['id']} fit {row['fit']}: {row['reason']}")
        if proof.get("pretick"):
            out += ["", "Why no strong, trusted find was pre-ticked:"]
            for row in proof["pretick"]:
                out.append(f"- {row['id']} fit {row['fit']} (job \"{row['job']}\", "
                           f"{row['job_source']}; brain: {row['judge']}): {row['why']}")
    if r.get("bar") and not r["bar"]["pass"] or r.get("show_hits"):
        out += ["", "Raw finds and why each was left out (best first):", ""]
        finds = sorted(r["evidence"].get("finds") or [],
                       key=lambda f: -((f.get("meaning") or {}).get("fit") or
                                       (f.get("meaning") or {}).get("blurb") or -1))
        for f in finds[:80]:
            m = f.get("meaning") or {}
            tr = f.get("trust") or {}
            out.append(f"- {f['id']} [{f.get('part')}, mode {f.get('mode')}, "
                       f"trust {tr.get('path') or ','.join(tr.get('reasons') or []) or '-'}] "
                       f"blurb {m.get('blurb')} fit {m.get('fit')} -> {m.get('state')}"
                       f"{' (' + str(m.get('why_dropped')) + ')' if m.get('why_dropped') else ''}"
                       f": {f.get('what') or ''}"[:300])
        if len(finds) > 80:
            out.append(f"- ... and {len(finds) - 80} more in the JSON file")
    out.append("")
    return out


def write_reports(results: list[dict], out_dir: str, *, stem: str, header: dict) -> tuple:
    os.makedirs(out_dir, exist_ok=True)
    js = os.path.join(out_dir, stem + ".json")
    md = os.path.join(out_dir, stem + ".md")
    with open(js, "w", encoding="utf-8") as fh:
        json.dump({"header": header, "goals": results}, fh, indent=1, ensure_ascii=False,
                  default=str)
    lines = [f"# {header.get('title', 'Goal measurement')}", ""]
    lines += [f"- {k}: {v}" for k, v in header.items() if k != "title"] + [""]
    for r in results:
        lines += goal_md(r)
    with open(md, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    return js, md


# -- main -------------------------------------------------------------------------------------
def parse(argv: Optional[list[str]] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--goals", default=None)
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry", action="store_true",
                      help="needs and this computer only: every outside request is refused "
                           "(and listed in the report)")
    mode.add_argument("--live", action="store_true", help="search online too")
    mode.add_argument("--discovery", action="store_true", help="the discovery bar")
    ap.add_argument("--keyless", action="store_true")
    ap.add_argument("--integrate", action="store_true")
    ap.add_argument("--base-url", default="http://127.0.0.1:8000")
    ap.add_argument("--api-key-file", default=None)
    ap.add_argument("--pace", type=float, default=65.0)
    ap.add_argument("--out", default=os.path.join(os.getcwd(), "r3_acceptance_out"))
    ap.add_argument("--run", default=None, help="name of this run (discovery file names)")
    ap.add_argument("--only", default=None, help="comma-separated goal ids")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--show-hits", action="store_true")
    ap.add_argument("--reuse-needs", default=None,
                    help="an earlier report or discovery JSON whose jobs are used again")
    return ap.parse_args(argv)


def main(argv: Optional[list[str]] = None, *, sleep: Callable[[float], None] = time.sleep,
         say: Callable[[str], None] = print) -> int:
    args = parse(argv)
    if args.discovery:
        args.keyless = True
    if args.keyless:
        why = github_key_present()
        if why:
            say(f"Refusing to start a keyless run: {why}.")
            raise Refused(2)
    online = bool(args.live or args.discovery)
    goals = load_goals(args.goals or (DISCOVERY_GOALS if args.discovery else DEFAULT_GOALS),
                       args.only.split(",") if args.only else None)
    cfg = {"ollama_url": os.environ.get("PUFFPAI_OLLAMA_URL") or "http://127.0.0.1:11435",
           "chosen_model": args.model}
    capture = Capture(refuse=not online).install()
    http = None
    if args.integrate:
        key = ""
        if args.api_key_file:
            with open(args.api_key_file, encoding="utf-8") as fh:
                key = fh.read().strip()
        http = Http(args.base_url, key)
    run = args.run or datetime.datetime.now().strftime("%Y%m%d_%H%M")

    def header(results: list[dict]) -> dict:
        recalls = [r["recall"]["recall"] for r in results
                   if r["recall"].get("recall") is not None]
        head = {"title": "Discovery bar" if args.discovery else "Goal measurement",
                "when": datetime.datetime.now().isoformat(timespec="seconds"),
                "mode": "discovery" if args.discovery else "live" if args.live else "dry",
                "keyless": bool(args.keyless), "model": args.model, "goals": len(results),
                "mean_recall": round(sum(recalls) / len(recalls), 3) if recalls else None,
                "min_recall": min(recalls) if recalls else None}
        if any(r.get("bar") for r in results):
            head["bar_passed"] = sum(1 for r in results if r.get("bar", {}).get("pass"))
            head["bar_passed_pretick_not_tried"] = sum(
                1 for r in results if r.get("bar", {}).get("pass")
                and r["bar"].get("pretick") == "not_tried")
        return head

    reuse: dict[str, dict] = {}
    if args.reuse_needs:
        with open(args.reuse_needs, encoding="utf-8") as fh:
            for row in json.load(fh).get("goals") or []:
                if isinstance(row, dict) and isinstance(row.get("needs"), dict):
                    reuse[str(row.get("id"))] = row["needs"]
    results = []
    try:
        for n, goal in enumerate(goals):
            if n and online and args.pace > 0:
                say(f"waiting {args.pace:.0f} s so searches stay under 10 a minute")
                sleep(args.pace)
            say(f"goal {goal.get('id')}")
            r = run_goal(goal, cfg=cfg, online=online, capture=capture, say=say,
                         reuse=reuse.get(str(goal.get("id"))))
            r["show_hits"] = bool(args.show_hits)
            if args.discovery or online:
                r["bar"] = bar_for(r)
                if not r["bar"]["pass"]:
                    r["bar"]["proof"] = failure_proof(r)
                    say(f"  proof: {r['bar']['proof']['verdict']}")
                say(f"  bar: {'PASS' if r['bar']['pass'] else 'FAIL'} {r['bar']['checks']}"
                    f" pre-tick: {r['bar']['pretick']}")
            if http is not None:
                r["integration"] = integrate(goal, http, name=f"R3{n + 1}Bot")
            results.append(r)
            if args.discovery:
                # Written after every goal, so a run that stops keeps what it measured.
                write_reports(results, args.out, stem=f"F_discovery_{run}",
                              header=dict(header(results), partial=True))
    finally:
        capture.uninstall()
    if args.discovery:
        js, md = write_reports(results, args.out, stem=f"F_discovery_{run}",
                               header=header(results))
    else:
        js, md = write_reports(results, args.out, stem="report", header=header(results))
    say(f"wrote {js} and {md}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
