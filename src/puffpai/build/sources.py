"""Data sources' places on the Bridge: one switch per outside host a job's
data comes from.

A website from BuildBot's own list becomes a live card with its own switch
(``assemble.seed_switch``). Every OTHER data source a job uses (a data skill's
own service, a public API from another list, a source the assembled view
reads) gets the same kind of switch here: an ordinary app door on the Bridge,
``app:src_<hash>:feed``, for exactly that one host, registered with the live
cards' doors (``feeds.sync_doors``) and shut until the owner's recorded yes.

It is listed on "What leaves your computer" as a switch (``card-src_<hash>``),
so it goes the way every switch goes: its row on the build's setup list, the
server's sentence, an ``outside_on`` ticket for this device and exactly this
switch (``fix_api``), the yes recorded per host set. Her one Yes on the list
covers it; the Bridge page can switch it off again at any time. It has no
page and no tile of its own: it is an ingredient of its job.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any, Optional

from puffpai.logging_config import get_logger

logger = get_logger(__name__)

PREFIX = "src_"
CARD_PREFIX = "card-"
#: Part types that already have a place of their own (a live card, an app).
_OWN_PLACE = frozenset({"feed", "assembled", "routine"})
DATA_SENT = "What your bot asks it for, and your internet address."


def source_id(host: str) -> str:
    """``src_<12 hex>`` for one host (fits the Bridge's app id rule)."""
    return PREFIX + hashlib.sha256(str(host or "").lower().encode("utf-8")).hexdigest()[:12]


def switch_id(host: str) -> str:
    """The switch's id on the Bridge page and the setup list."""
    return CARD_PREFIX + source_id(host)


def door_id(host: str) -> str:
    from puffpai.security import egress_doors
    return egress_doors.app_door_id(source_id(host))


def is_source(sid: Any) -> bool:
    return str(sid or "").startswith(CARD_PREFIX + PREFIX)


#: A URL in text (http or https), its host and the rest.
_URL_RE = re.compile(r"https?://([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)\.?(?::\d{2,5})?"
                     r"(/[^\s\"'`)<>\]]*)?", re.I)
#: Code in a skill's text: fenced blocks and inline code.
_FENCE_RE = re.compile(r"(?ms)^[ \t]*(`{3,}|~{3,})[^\n]*\n(.*?)(?:^[ \t]*\1[ \t]*$|\Z)")
_INLINE_RE = re.compile(r"`([^`\n]{4,600})`")
#: Frontmatter fields that are a skill's page, never a call on their own.
_HOME_FIELDS = ("homepage", "home_page", "website", "url", "documentation", "docs")
#: Frontmatter fields that declare what a skill calls.
_API_FIELDS = ("api", "api_base", "apibase", "api_url", "apiurl", "base_url", "baseurl",
               "endpoint", "server_url", "mcp", "mcp_url", "openapi", "openapi_url")
#: The most hosts one part may list (every one is its own switch row).
HOSTS_MAX = 6


def _clean(host: Any) -> str:
    from puffpai.security import egress_doors
    h = str(host or "").strip().rstrip(".").lower()
    return h if h and egress_doors.is_registrable_hostname(h) else ""


def _not_service(host: str) -> bool:
    """Where a part comes from or is documented, never what it calls."""
    from puffpai import skills
    from puffpai.build import classify
    return host in classify.HOME_HOSTS or host.endswith(".github.io") \
        or not skills._service_host(host)  # noqa: SLF001 - one rule for both


def _url_host(url: Any) -> str:
    m = _URL_RE.match(str(url or "").strip())
    return _clean(m.group(1)) if m else ""


def _skill_text(part: dict) -> tuple[str, str]:
    """``(SKILL.md text, folder)`` of a skill part: its own text when the
    plan has it, else the installed copy. ("", "") when neither."""
    for key in ("skill_md", "text"):
        text = part.get(key)
        if isinstance(text, str) and text.startswith("---"):
            return text, ""
    name = str(part.get("folder") or part.get("skill") or "")
    if not name:
        return "", ""
    try:
        import os

        from puffpai import skills
        rec = skills.find(name)
        if not rec:
            return "", ""
        with open(rec["path"], encoding="utf-8", errors="replace") as fh:
            return fh.read(512 * 1024), os.path.dirname(str(rec["path"]))
    except Exception:  # noqa: BLE001 - unreadable: nothing known
        return "", ""


def _code_hosts(body: str) -> list[str]:
    """Hosts of every URL in a text's code (fenced blocks and inline code)."""
    out: list[str] = []
    snippets = [block for _f, block in _FENCE_RE.findall(body or "")]
    snippets += _INLINE_RE.findall(body or "")
    for snippet in snippets:
        for m in _URL_RE.finditer(snippet):
            h = _clean(m.group(1))
            if h and h not in out:
                out.append(h)
    return out


def skill_calls(text: str, folder: str = "") -> list[str]:
    """Every host a skill's text shows it CALLING: its one keyed request,
    the API base or tool address its frontmatter declares, the API calls its
    text names (a verb before it, an api host or path), and every URL in its
    code examples (fenced or inline). Its homepage is never one on its own:
    it counts only when the text also calls it. Reads only."""
    from puffpai import skills
    meta, body = skills.parse_frontmatter(text or "")
    meta = meta if isinstance(meta, dict) else {}
    out: list[str] = []

    def add(h: Any) -> None:
        h = _clean(h)
        if h and not _not_service(h) and h not in out:
            out.append(h)
    for h in (skills.key_request_service(text, folder) or {}).get("hosts") or []:
        add(h)
    how = skills.how_it_runs_text(text, folder)
    if how.get("endpoint"):
        add(_url_host(how["endpoint"]))
    for key in _API_FIELDS:
        if isinstance(meta.get(key), str):
            add(_url_host(meta[key]))
    home = {_url_host(meta.get(k)) for k in _HOME_FIELDS} - {""}
    code = _code_hosts(body)
    for h in how.get("hosts") or []:
        # how_it_runs_text falls back to the homepage when it found nothing
        # else; that fallback is not a call.
        if h not in home or h in code:
            add(h)
    for h in code:
        add(h)
    return out[:HOSTS_MAX]


def part_hosts(part: dict) -> list[str]:
    """Every outside host a non-website data source part CALLS, in order;
    [] when it calls none. A skill: :func:`skill_calls` over its text (the
    plan's copy, else the installed one); none when no text can be read. Any
    other part: the hosts its catalog row names, its sample and lookup
    addresses, a tool server's address; its page address only when nothing
    else is known (an API row whose address is its base). Never a homepage
    on its own (else "Turn on News" would switch on the skill's homepage).
    """
    if str(part.get("type") or "") in _OWN_PLACE or part.get("builtin"):
        return []
    if str(part.get("service") or part.get("ref") or "").startswith("seed:"):
        return []                      # a website from our own list: a live card
    out: list[str] = []

    def add(h: Any) -> None:
        h = _clean(h)
        if h and not _not_service(h) and h not in out:
            out.append(h)
    f = part.get("facts") if isinstance(part.get("facts"), dict) else {}
    if part.get("type") == "skill":
        text, folder = _skill_text(part)
        if text:
            for h in skill_calls(text, folder):
                add(h)
        # No text to read: what it calls is unknown, so nothing is listed
        # (fail closed; a link host may be its homepage).
        return out[:HOSTS_MAX]
    for h in list(part.get("hosts") or []) + list(f.get("hosts") or []):
        add(h)
    for key in ("sample_endpoint", "sample_url", "lookup_url"):
        add(_url_host(part.get(key)))
    mcp = part.get("mcp") if isinstance(part.get("mcp"), dict) else {}
    add(_url_host(mcp.get("url")))
    if not out:
        add(_url_host(part.get("url")))
    return out[:HOSTS_MAX]


def host_of(part: dict) -> str:
    """The first outside host a non-website data source part calls, or ""
    (:func:`part_hosts` lists them all)."""
    hosts = part_hosts(part)
    return hosts[0] if hosts else ""


def _wanted(build: Optional[dict] = None) -> list[dict]:
    """``[{"id", "host", "name", "ref"}]``: one per source switch row the
    build's setup list SHOWS for a job that is not route D. The list is the
    one truth: a door exists only for a host a row names."""
    try:
        from puffpai.build import schema as S
        build = build if build is not None else S.load_build()
    except Exception:  # noqa: BLE001 - no build record: no sources
        return []
    open_ids = {j.get("id") for j in build.get("jobs") or [] if j.get("route") != S.D}
    parts = build.get("parts") or {}
    out: dict[str, dict] = {}
    for r in build.get("setup") or []:
        if not isinstance(r, dict) or r.get("kind") != "switch" \
                or not is_source(r.get("service")):
            continue
        if not set(r.get("job_ids") or []) & open_ids:
            continue
        host = _clean(r.get("host"))
        if not host or switch_id(host) != r.get("service") or source_id(host) in out:
            continue
        part = parts.get(r.get("ref")) if isinstance(parts.get(r.get("ref")), dict) else {}
        out[source_id(host)] = {"id": source_id(host), "host": host,
                                "name": str(part.get("name") or host)[:64],
                                "ref": str(r.get("ref") or "")}
    return list(out.values())


def door_entries(build: Optional[dict] = None) -> list[tuple[str, dict]]:
    """Entries for ``egress_doors.register_app_doors`` (with the live cards')."""
    return [(s["id"], {"hosts": [s["host"]], "name": s["name"],
                       "purpose": f"Data for your bot from {s['name']}.",
                       "data_sent": DATA_SENT}) for s in _wanted(build)]


def _consent(src: str, host: str) -> Optional[bool]:
    from puffpai.security import egress_doors
    try:
        entry = egress_doors._read_app_consent().get(  # noqa: SLF001
            egress_doors.app_door_flag(src, [host]))
    except Exception:  # noqa: BLE001 - unreadable: no yes
        return None
    return entry.get("open") if isinstance(entry, dict) and \
        isinstance(entry.get("open"), bool) else None


def is_on(host: str) -> bool:
    """Did she say yes to this host's switch (its recorded yes)?"""
    return _consent(source_id(host), host) is True


def outside_rows(row: Any, posture: str, open_posture: str) -> list[dict]:
    """Rows for "What leaves your computer", in its ``_row`` shape (*row* is
    ``outside_services._row``). Never raises."""
    out = []
    for s in _wanted():
        said = _consent(s["id"], s["host"])
        on = said is True and posture == open_posture
        if on:
            line = "Switched on."
        elif said is True:
            line = ("You said yes, but your bot is set to stay off the internet, "
                    "so this is off.")
        else:
            line = "Switched off."
        can = posture != "sealed"
        out.append(row(CARD_PREFIX + s["id"], s["name"],
                       f"Data for your bot from {s['name']}: {s['host']}, and the picture "
                       f"sites its results point to.", DATA_SENT, [s["host"]],
                       "on" if on else "off", line, can_switch=can,
                       why_cannot_switch="" if can else (
                           "Your bot is set to keep everything on this computer, so "
                           "this cannot be switched on."),
                       turned_on_by="you" if on else "", kind="card"))
    return out


__all__ = ["CARD_PREFIX", "HOSTS_MAX", "PREFIX", "door_entries", "door_id", "host_of", "is_on",
           "is_source", "outside_rows", "part_hosts", "skill_calls", "source_id", "switch_id"]
