"""Find: every candidate for the jobs, from every place, in ONE shape.

Places: her own GitHub projects and the ones she starred, when she connected
her account (:func:`puffpai.github_account.candidates_for`, origin "From your
GitHub", listed first so the same project found by a search keeps that mark),
the shelves and code sites (:func:`puffpai.finding.find.search_jobs`),
the live data lists (:func:`puffpai.catalogs.live.discover_live`, which
holds our own hand-checked website rows first), and the bot's own built-in
reminders. A test (or Find more) passes ``catalog(jobs, goal) -> [cand]``
instead.

A candidate::

    {"ref", "name", "type", "what", "source", "url", "hosts": [...],
     "needs": [...], "skill_md"?, "files"?, "mcp"?, "tools"?, "mode"?,
     "maybe_hosts"?, "app_id"?, "service"?, "switch"?, "caps"?, "builtin"?}

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import urllib.parse
from typing import Any, Callable, Optional

from puffpai.logging_config import get_logger

logger = get_logger(__name__)

LIVE_PER_JOB = 6
SKILL_TEXTS_MAX = 16

ROUTINE = {"ref": "builtin:routine", "name": "Your bot's reminders", "type": "routine",
           "what": "Reminds you at the time you pick, once or every day.", "source": "builtin",
           "builtin": "routine", "hosts": [], "needs": []}


def _host(url: Any) -> str:
    try:
        return (urllib.parse.urlsplit(str(url or "")).hostname or "").lower()
    except ValueError:
        return ""


def from_find(f: dict) -> dict:
    """A shelf or code-site find as a candidate."""
    raw = f.get("raw") if isinstance(f.get("raw"), dict) else {}
    c = {"ref": str(f["id"]), "name": str(f.get("name") or f["id"]),
         "type": str(f.get("type") or ""), "what": str(f.get("what") or ""),
         "source": str(f.get("site") or "find"), "url": str(f.get("url") or ""),
         "hosts": [], "needs": [], "caps": [str(x) for x in f.get("caps") or []
                                            if isinstance(x, str)]}
    if f.get("cap"):
        c["caps"].append(str(f["cap"]))
    if c["type"] == "mcp":
        server = raw.get("server") if isinstance(raw.get("server"), dict) else {}
        remotes = server.get("remotes") if isinstance(server.get("remotes"), list) else []
        url = next((str(r.get("url")) for r in remotes if isinstance(r, dict) and r.get("url")), "")
        c["mcp"] = {"url": url} if url else {"command": "stdio"}
        if url:
            c["hosts"] = [_host(url)]
    if f.get("skill_md"):
        c["skill_md"] = str(f["skill_md"])
        c["files"] = list(f.get("files") or [])
    if c["source"] in CODE_FIND_SITES:
        _code_facts(c, f, raw)
    return c


#: Where a find is a code project BuildBot can download and run itself.
CODE_FIND_SITES = ("awesome-selfhosted", "github", "codeberg")


def _code_facts(c: dict, f: dict, raw: dict) -> None:
    """What a code site or the app list says about how a project runs and
    how alive it is (classify and decide read these; nothing downloaded)."""
    item = raw.get("item") if isinstance(raw.get("item"), dict) else {}
    if f.get("platforms"):
        c["platforms"] = [str(p) for p in f["platforms"]][:12]
    tags = [str(t) for t in (f.get("tags") or item.get("topics") or [])][:12]
    if tags:
        c["topics" if item else "tags"] = tags
    if item.get("language"):
        c["language"] = str(item["language"])[:30]
    stars = (f.get("popularity") or {}).get("stars")
    if isinstance(stars, int):
        c["stars"] = stars
    if item.get("pushed_at"):
        c["pushed_at"] = str(item["pushed_at"])[:10]
    if f.get("site") == "awesome-selfhosted":
        c["listed"] = True
    # The licence as the list or code site shows it (a note on the yes row
    # only; the files' own licence is read after the download).
    lics = [str(x)[:40] for x in (f.get("licences") or []) if x][:4]
    if lics:
        c["licences"] = lics
    if f.get("licence"):
        c["licence"] = str(f["licence"])[:60]
    words = " ".join(tags)
    c["text"] = f"{c['name']}. {c['what']} {words}".strip()[:600]


def project_key(c: dict) -> str:
    """``host/owner/name`` of a code find's project, or ""."""
    if str(c.get("source") or "") not in CODE_FIND_SITES:
        return ""
    parts = urllib.parse.urlsplit(str(c.get("url") or ""))
    path = [p for p in parts.path.split("/") if p]
    if not parts.hostname or len(path) < 2:
        return ""
    name = path[1][:-4] if path[1].endswith(".git") else path[1]
    return f"{parts.hostname.lower()}/{path[0].lower()}/{name.lower()}"


def code_item(c: dict) -> str:
    """The item id the integrate path downloads a code find by
    (``wizard.find_parts`` holds its pinned ``found/v1`` record), or "" when
    it cannot be read now. Reads the project's facts at its current commit
    through the Bridge (the existing readers); downloads nothing."""
    try:
        from puffpai.finding import find as finding_find
        from puffpai.finding import install
        f = finding_find.recall(c.get("ref")) or {}
        site = str(f.get("site") or c.get("source") or "")
        if site in ("github", "codeberg") and f:
            found = install._round3_found(f)  # noqa: SLF001 - the one reader
        elif site == "awesome-selfhosted":
            found = _listed_found(c, f)
        else:
            return ""
        if not isinstance(found, dict) or not (found.get("repo") or {}).get("pinned_commit"):
            return ""
        return install._app_item(f or {"id": str(c["ref"])}, found)  # noqa: SLF001
    except Exception as exc:  # noqa: BLE001 - not readable now: the check says so
        logger.info("build find: code find not read (%s)", type(exc).__name__)
        return ""


def _listed_found(c: dict, f: dict) -> Optional[dict]:
    """The app list's row as a pinned ``found/v1`` record (its source
    project read at its current commit), or None."""
    from puffpai.catalogs import search_budget, where_to_look
    from puffpai.catalogs.searchers import awesome_selfhosted as A
    from puffpai.finding import app_catalog
    rid = str(((f.get("raw") or {}).get("row_id")) or "")
    row = next((r for r in app_catalog.rows() if str(r.get("id")) == rid), None) if rid         else None
    if row is None:
        row = A.listed(c.get("url"))
    if not row or not row.get("source_code_url"):
        return None
    found = A._found(row, "code-app")  # noqa: SLF001 - the list's own record shape
    return where_to_look.details(found, budget=search_budget.new_budget(wizard=False))


_MEANINGS: Optional[dict] = None
_EXTRAS: Optional[dict] = None
#: What our own website rows say about their answers, kept on the part.
SEED_EXTRAS = ("time_base", "value_words")


def _seed_meanings() -> dict:
    """Our own website rows' documented field scales (``field_meanings``)."""
    global _MEANINGS
    if _MEANINGS is None:
        try:
            from puffpai.catalogs import api_seeds
            _MEANINGS = {str(r["id"]): r["field_meanings"] for r in api_seeds.load_rows()
                         if isinstance(r.get("field_meanings"), dict)}
        except Exception:  # noqa: BLE001 - no scales: the brain labels them
            _MEANINGS = {}
    return _MEANINGS


def _seed_extras() -> dict:
    """Our own rows' time base (when each row is: a start time plus an
    offset) and words for scale values (``value_words``)."""
    global _EXTRAS
    if _EXTRAS is None:
        _EXTRAS = {}
        try:
            import json
            import os
            from puffpai.catalogs import live
            path = os.path.join(live.repo_root(), "config", "catalog_seeds",
                                "api_seed_extras.json")
            with open(path, encoding="utf-8") as fh:
                rows = (json.load(fh) or {}).get("rows") or {}
            _EXTRAS = {str(sid): {k: v[k] for k in SEED_EXTRAS if isinstance(v.get(k), dict)}
                       for sid, v in rows.items() if isinstance(v, dict)}
        except (OSError, ValueError, AttributeError):
            _EXTRAS = {}
    return _EXTRAS


def from_live(cand: dict) -> dict:
    """A live data row (our own list, APIs.guru, public-apis) as a candidate."""
    terms = " ".join(cand.get("match_terms") or [])
    sample = str(cand.get("sample_endpoint") or cand.get("url") or "")
    out = {"ref": str(cand["id"]), "name": str(cand.get("name") or cand["id"]),
           "type": "live_data", "what": str(cand.get("what_it_is") or ""),
           "text": f"{cand.get('name', '')}. {cand.get('what_it_is', '')} {terms}".strip(),
           "source": str(cand.get("source_catalog") or "live"),
           # The address the made view reads: the row's own card address
           # (with the owner's town filled in after her yes) when it has
           # one, else its sample; the sample is what is looked at first.
           "url": str(cand.get("card_url") or sample), "sample_url": sample,
           "hosts": [str(cand["host"])] if cand.get("host") else [],
           "needs": ["key" if n == "key" else str(n) for n in cand.get("needs") or []],
           "auth": str(cand.get("auth") or ""), "service": str(cand["id"])}
    # What the row says about its own answer: how a picture's name becomes
    # its address, and what each number means.
    if cand.get("image_template"):
        out["image_template"] = str(cand["image_template"])
    meanings = cand.get("field_meanings")
    if not isinstance(meanings, dict) and out["ref"].startswith("seed:"):
        meanings = _seed_meanings().get(out["ref"].split(":", 1)[1])
    if isinstance(meanings, dict) and meanings:
        out["meanings"] = {str(k): str(v)[:200] for k, v in meanings.items()}
    if out["ref"].startswith("seed:"):
        out.update(_seed_extras().get(out["ref"].split(":", 1)[1]) or {})
    if cand.get("what_it_is"):
        out["about"] = str(cand["what_it_is"])[:300]
    if cand.get("lookup_url"):
        out["lookup_url"] = str(cand["lookup_url"])
    if cand.get("card_topic"):
        out["topic"] = str(cand["card_topic"])[:40]     # its address's {topic} when none is asked
    return out


def skill_text(c: dict) -> tuple[str, list[str]]:
    """``(SKILL.md text, file names)`` for a skill find, read from its shelf
    (the same reads the install preview makes). ("", []) when unknown."""
    from puffpai.finding import find as finding_find
    from puffpai.finding import install, net, sites
    f = finding_find.recall(c["ref"])
    if not f:
        return "", []
    try:
        kind, ref = install._skill_source(f)  # noqa: SLF001
        if kind == "clawhub":
            v = sites.clawhub_verify(ref["slug"], ref["owner"])
            if not v:
                return "", []
            files = [x["path"] for x in v["files"]]
            return install._clawhub_skill_md(ref["slug"], ref["owner"],  # noqa: SLF001
                                             v.get("version", "")), files
        if kind == "github":
            sha = install._gh_commit(ref["repo"], ref["service"])  # noqa: SLF001
            tree = install._gh_tree(ref["repo"], sha, ref["service"]) if sha else []  # noqa: SLF001
            folder = install.skill_folder_in(tree, ref["skill"], ref["hint"])
            if folder is None:
                return "", []
            prefix = folder + "/" if folder else ""
            files = [t["path"][len(prefix):] for t in tree if t.get("type") == "blob"
                     and t["path"].startswith(prefix) and (folder or "/" not in t["path"])]
            ans = net.get(f"https://{sites.GITHUB_RAW}/{ref['repo']}/{sha}/"
                          + urllib.parse.quote(prefix + "SKILL.md", safe="/"),
                          hosts=[sites.GITHUB_RAW], service=ref["service"],
                          max_bytes=install.MAX_FILE_BYTES, cache_s=600)
            return (ans["body"].decode("utf-8", errors="replace") if ans.ok else ""), files
    except Exception as exc:  # noqa: BLE001 - unknown text: classified as a guide
        logger.info("build find: skill text not read (%s)", type(exc).__name__)
    return "", []


def add_skill_texts(cands: list[dict], limit: int = SKILL_TEXTS_MAX) -> None:
    """Read the SKILL.md of up to *limit* skill finds so they classify by how
    they run (a skill whose text is not read classifies as a guide)."""
    n = 0
    for c in cands:
        if c.get("type") != "skill" or c.get("skill_md") or n >= limit:
            continue
        text, files = skill_text(c)
        n += 1
        if text:
            c["skill_md"], c["files"] = text, files


def search_jobs_for(jobs: list[dict], goal: str) -> list[dict]:
    """The finding search over our jobs (its own job shape)."""
    from puffpai.finding import find as finding_find
    asks = [{"id": j["id"], "name": j["name"], "words": " ".join(j.get("subject") or [])
             or j["name"].lower(), "does": j.get("does", ""), "books": [],
             **({"cap": j["cap"]} if j.get("cap") else {})} for j in jobs]
    try:
        got = finding_find.search_jobs(asks, subject=goal, with_finds=True)
    except Exception as exc:  # noqa: BLE001 - one place failing never stops the rest
        logger.warning("build find: shelves not searched (%s)", type(exc).__name__)
        return []
    return [from_find(f) for f in got.get("finds") or []]


def hers_for(jobs: list[dict]) -> list[dict]:
    """Her own GitHub projects that fit a job's words (her list is read again
    on a build). [] when she has not connected GitHub."""
    try:
        from puffpai import github_account
        return github_account.candidates_for(jobs)
    except Exception as exc:  # noqa: BLE001 - one place failing never stops the rest
        logger.info("build find: her projects not read (%s)", type(exc).__name__)
        return []


def have_for() -> list[dict]:
    """What is already part of this bot (installed skills, tool servers, apps
    and browser apps): used as it is, never installed again."""
    try:
        from puffpai.build import migrate
        return [c for c in migrate.installed_cands({}) if c.get("type") != "library"]
    except Exception as exc:  # noqa: BLE001
        logger.info("build find: installed things not read (%s)", type(exc).__name__)
        return []


def apps_for(jobs: list[dict], goal: str) -> list[dict]:
    """Ready-made apps per job: GitHub by stars, README run steps (build.apps)."""
    try:
        from puffpai.build import apps
        return apps.for_jobs(jobs, goal)
    except Exception as exc:  # noqa: BLE001 - one place failing never stops the rest
        logger.info("build find: apps not searched (%s)", type(exc).__name__)
        return []


def live_for(jobs: list[dict]) -> list[dict]:
    from puffpai.catalogs import live
    out: list[dict] = []
    for j in jobs:
        terms = list(j.get("subject") or []) + j["name"].lower().split()
        try:
            rows = live.discover_live(terms, limit=LIVE_PER_JOB)
        except Exception as exc:  # noqa: BLE001
            logger.info("build find: live list not read (%s)", type(exc).__name__)
            rows = []
        out.extend(from_live(r) for r in rows)
    return out


def find(jobs: list[dict], goal: str = "", *,
         catalog: Optional[Callable[[list, str], list]] = None,
         read_skills: bool = True) -> list[dict]:
    """Every candidate for *jobs*, once each (first one wins), plus the
    built-in reminders when a job reminds."""
    if catalog is not None:
        found = list(catalog(jobs, goal) or [])
    else:
        # 2. What she already has (installed skills, tool servers, apps) is
        # used as it is; 4. ready-made apps from GitHub by stars come first
        # among the new finds (they ask before the other searches spend
        # GitHub's allowance), then the shelves, code sites and data lists.
        apps = apps_for(jobs, goal)
        # A job that has ready-made apps needs nothing else looked for.
        covered = {jid for c in apps for jid in c.get("for_jobs") or []}
        rest = [j for j in jobs if j["id"] not in covered]
        found = (have_for() + hers_for(jobs) + apps
                 + (search_jobs_for(rest, goal) + live_for(rest) if rest else []))
        if read_skills:
            add_skill_texts(found)
    out: dict[str, dict] = {}
    projects: set[str] = set()
    for c in found:
        if isinstance(c, dict) and c.get("ref") and c["ref"] not in out:
            # The same project from the app list and from a code site is one
            # find (the first, the app list's, is kept).
            key = project_key(c)
            if key and key in projects:
                continue
            if key:
                projects.add(key)
            out[c["ref"]] = dict(c)
    if any(j.get("action") == "remind" for j in jobs) and ROUTINE["ref"] not in out:
        out[ROUTINE["ref"]] = dict(ROUTINE)
    return list(out.values())


__all__ = ["CODE_FIND_SITES", "ROUTINE", "add_skill_texts", "code_item", "find", "from_find", "from_live", "hers_for", "project_key",
           "live_for",
           "search_jobs_for", "skill_text"]
