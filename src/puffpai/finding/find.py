"""``find_things``: one search over the skill shelves, the tool shelf, and the
apps and code other people built, grouped by her jobs and labelled by type.

Rule copied from OpenClaw's ``clawhub`` skill: search before saying you
can't. A search finds and shows; it never installs (``install.py`` does that,
after she says yes).

Two ways in:

* :func:`find_things` (words, kinds): one job named by the words. The chat
  tool and the Add page use it.
* :func:`find_for_goal` (her goal sentence): the bot's brain turns the goal
  into its kind and a few jobs (``wizard.needs_llm``, the same call the wizard
  uses), and each job is searched. The wizard's "Finding things for your bot"
  screen and the Add page use it.

Every site is asked in parallel, and each site's own order is kept.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import concurrent.futures as cf
import json
import os
import re
import threading
from typing import Any, Callable, Iterable, Optional

from puffpai.finding import books, hermes_index, net, sites
from puffpai.finding import wording as W
from puffpai.logging_config import get_logger

logger = get_logger(__name__)

#: How many finds each site may show for one job.
SHOW_PER_SITE = 4
#: Jobs searched for one goal: the whole bot, then up to five of its jobs.
MAX_JOBS = 6
WORKERS = 12
#: Library searches sent together after the subject's own (each library
#: answers one a second and is waited on for a few seconds).
BOOK_WAVE = 4
#: A book search asked again with one word keeps its answers after the
#: first searches (their order, when merged).
RETRY_OFFSET = 100
#: ClawHub's own check is read for this many ClawHub finds a search.
VERIFY_MAX = 16
STORE_MAX = 800

#: Which sites a type is looked for on.
SITES_FOR_TYPE: dict[str, tuple[str, ...]] = {
    "skill": ("clawhub", "skills.sh", "hermes"),
    "mcp": ("mcp-registry", "npm", "pypi", "github"),
    "browser_app": ("github", "codeberg"),
    "data": ("github", "codeberg"),
    "server_app": ("github", "codeberg", "awesome-selfhosted"),
    "cli": ("github", "codeberg", "npm", "pypi"),
    "library": ("github", "codeberg", "npm", "pypi"),
    "book": ("libraries",),
}
#: In this order: apps first (the app list, then code sites with apps), then
#: skills and tool servers, then packages and books (use the apps that
#: already do the job).
ALL_SITES = ("awesome-selfhosted", "github", "codeberg", "clawhub", "skills.sh", "hermes",
             "mcp-registry", "npm", "pypi", "libraries")
#: Sites matched by meaning: their finds are not screened by shared words
#: (the build's decide step judges them by meaning and subject).
MEANING_SITES = frozenset({"awesome-selfhosted"})

_SEARCHERS: dict[str, Callable[[str, int], list[dict]]] = {
    "clawhub": sites.clawhub, "skills.sh": sites.skills_sh,
    "hermes": lambda w, n: hermes_index.search(w, n),
    "mcp-registry": sites.mcp_registry, "github": sites.github, "codeberg": sites.codeberg,
    "npm": sites.npm, "pypi": sites.pypi, "awesome-selfhosted": sites.awesome_selfhosted,
    "libraries": lambda w, n: books.search(w, n, wide=True),
}

_STORE_LOCK = threading.Lock()

#: The skill shelves: each is asked for the whole phrase AND its key single
#: words, because a shelf's search often misses a good skill for a longer
#: phrase ("keep track of our filament" misses Filament Vault, "filament"
#: finds it). The code sites keep one search a job (GitHub allows few).
WORD_SHELVES = ("clawhub", "skills.sh", "hermes")
#: Key single words searched besides the phrase, at most. Words shorter
#: than 4 letters ("3d") are too broad to search alone.
KEY_WORDS_MAX = 3
#: Words that say nothing about the subject: small words and the everyday
#: job words every search has ("keep track of", "help me find", "a tool").
_GENERIC = frozenset("""
a an the and or but for of to in on at by with from into onto over under about as
is are be been being was were it its this that these those there here than then
our my your their his her us we me you they them he she who what which when where how
some any all every each more most much many few new best good great simple easy free
small big long short own other another also just only very really please
keep keeps kept track tracks tracked tracking tracker trackers help helps helper helping
find finds finder finding make makes making made manage manages managing manager
management plan plans planning planner organize organise organizer organiser do does
doing done get gets getting use uses using used tell tells let lets show shows see
look looking want need needs come comes up out down off thing things something stuff
tool tools app apps application skill skills bot bots system software program code
library script service server data template list lists guide way ways idea ideas
""".split())


def key_words(text: Any, limit: int = KEY_WORDS_MAX) -> list[str]:
    """The words of *text* that name its subject (no small or generic words),
    in their order, at most *limit*."""
    out: list[str] = []
    for w in re.findall(r"[a-z0-9][a-z0-9'-]*", str(text or "").lower()):
        w = w.strip("'-")
        short = len(w) < 3 and not (w[:1].isdigit() and w[-1:].isalpha())  # "3d" counts
        if short or w.isdigit() or w in _GENERIC or w in out:
            continue
        out.append(w)
        if len(out) >= limit:
            break
    return out


def queries_for(site: str, words: str) -> list[str]:
    """What one site is asked for one job: the phrase, and on a skill shelf
    also its key single words."""
    queries = [words]
    if site in WORD_SHELVES and len(words.split()) > 1:
        singles = [w for w in key_words(words, limit=8) if len(w) >= 4]
        queries += [w for w in singles[:KEY_WORDS_MAX] if w != words.lower()]
    return queries


def _stem(word: str) -> str:
    return word[:5] if len(word) > 5 else word


def on_subject(f: dict, wanted: Iterable[str]) -> bool:
    """False only when a find clearly is about something else: its name and
    description share no subject word with the job or the bot (a genomics
    library for "track"). A job with no subject words passes everything."""
    stems = {_stem(w) for w in wanted if w}
    if not stems:
        return True
    text = f"{f.get('name') or ''} {f.get('what') or ''} {f.get('id') or ''}".lower()
    return any(s in text for s in stems)


def _merge(lists: list[list[dict]]) -> list[dict]:
    """One site's answers to several queries, turn by turn (each query keeps
    the site's own order), each find once."""
    out: list[dict] = []
    seen: set[str] = set()
    for row in range(max((len(x) for x in lists), default=0)):
        for finds in lists:
            if row < len(finds) and finds[row]["id"] not in seen:
                seen.add(finds[row]["id"])
                out.append(finds[row])
    return out


# ── kinds ───────────────────────────────────────────────────────────────────

def clean_kinds(kinds: Any) -> list[str]:
    """Type keys from her words or the model's ("skill", "MCP tool server" ...)."""
    if kinds in (None, "", []):
        return []
    if isinstance(kinds, str):
        kinds = re.split(r"[,;]", kinds)
    out: list[str] = []
    for k in kinds or []:
        key = W.KIND_ALIASES.get(" ".join(str(k).lower().split()))
        if key and key not in out:
            out.append(key)
    return out


def sites_for(kinds: list[str]) -> tuple[str, ...]:
    if not kinds:
        return ALL_SITES
    wanted: list[str] = []
    for k in kinds:
        for s in SITES_FOR_TYPE.get(k, ()):
            if s not in wanted:
                wanted.append(s)
    return tuple(s for s in ALL_SITES if s in wanted)


def _switched_off(site: str) -> bool:
    service = sites.SERVICE.get(site) or ""
    if not service:
        return False
    try:
        from puffpai.security import outside_switches
        return bool(outside_switches.is_off(service))
    except Exception:  # noqa: BLE001 - not knowing: fetch_small decides
        return False


def switch_on_shelves(by: str = "wizard") -> list[str]:
    """Switch on every shelf and code site the find step searches, except one
    she switched off herself. Run when she finishes "Finding things for your
    bot": that step told her where it looks, and her bot keeps finding things
    in chat afterwards. Returns the ids switched on now."""
    from puffpai.security import egress_doors, outside_switches
    done: list[str] = []
    for sid in dict.fromkeys(v for v in sites.SERVICE.values() if v):
        try:
            now = outside_switches.entry(sid) or {}
            if now.get("on") is True or (now.get("on") is False
                                         and now.get("by") == "owner"):
                continue          # on already, or she switched it off herself
            outside_switches.set_switch(sid, True, by=by)
            done.append(sid)
        except Exception as exc:  # noqa: BLE001 - one switch never stops the rest
            logger.warning("finding: %s not switched on (%s)", sid, type(exc).__name__)
    if done:
        egress_doors.reset_cache()
        try:
            from puffpai import outside_services
            for sid in done:
                outside_services._record_consent(sid, True, by)  # noqa: SLF001
        except Exception:  # noqa: BLE001
            pass
    return done


# ── the store of finds (so install_thing(id) knows what was shown) ──────────

def _store_path() -> str:
    from puffpai.config import get_state_dir
    return os.path.join(get_state_dir(), "finding", "finds.json")


def _load_store() -> dict[str, dict]:
    try:
        with open(_store_path(), encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def remember(finds: Iterable[dict]) -> None:
    with _STORE_LOCK:
        store = _load_store()
        for f in finds:
            old = store.pop(f["id"], None) or {}
            if old.get("cap") and not f.get("cap"):
                # A later plain search keeps the capability it was found for.
                f = dict(f, cap=old["cap"], **({"caps": old["caps"]} if old.get("caps") else {}))
            store[f["id"]] = f
        if len(store) > STORE_MAX:
            store = dict(list(store.items())[-STORE_MAX:])
        path = _store_path()
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(store, fh)
            os.replace(tmp, path)
        except OSError as exc:
            logger.warning("finding: finds could not be kept (%s)", type(exc).__name__)


def recall(find_id: Any) -> Optional[dict]:
    """A find shown earlier (with what install needs), or None."""
    with _STORE_LOCK:
        return _load_store().get(str(find_id or ""))


def public(f: dict) -> dict:
    """A find as the browser and the model see it (no raw site answer)."""
    return {k: v for k, v in f.items() if k != "raw"}


# ── searching ───────────────────────────────────────────────────────────────

def _one_site(site: str, words: str, limit: int) -> tuple[str, list[dict], bool]:
    net.begin()
    try:
        finds = _SEARCHERS[site](words, limit)
    except Exception as exc:  # noqa: BLE001 - one site never sinks the search
        logger.info("finding: %s search failed (%s)", site, type(exc).__name__)
        return site, [], True
    return site, list(finds or []), bool(net.failures()) and not finds


def _add_verdicts(finds: list[dict]) -> None:
    """ClawHub's own check, read for the ClawHub skills shown (in parallel)."""
    todo = [f for f in finds if f["id"].startswith("clawhub:") and not f.get("verdict")]
    todo = todo[:VERIFY_MAX]
    if not todo:
        return

    def _one(f: dict) -> None:
        ref = f["id"].split(":", 1)[1]
        owner, _, slug = ref.rpartition("/")
        try:
            v = sites.clawhub_verify(slug, owner)
        except Exception:  # noqa: BLE001
            v = None
        if v and v.get("verdict"):
            f["verdict"] = v["verdict"]
            f["verdict_words"] = W.verdict_words("ClawHub", v["verdict"])
            f["line"] = W.line_for(f)

    with cf.ThreadPoolExecutor(max_workers=min(WORKERS, len(todo))) as pool:
        list(pool.map(_one, todo))


def _group(job: dict, finds: list[dict]) -> dict:
    by_type: dict[str, list[dict]] = {}
    for f in finds:
        by_type.setdefault(f["type"], []).append(f)
    mine = sites.owner_language()
    types = []
    for t in W.TYPE_ORDER:
        if by_type.get(t):
            # Things that can trade money go last in their group; finds whose
            # words are in another language than hers go below the rest
            # (listed lower, never left out). Otherwise each site's order.
            items = sorted(by_type[t], key=lambda f: (bool(f.get("can_trade")),
                                                      sites.other_language(f, mine)))
            types.append({"type": t, "type_words": W.type_words(t),
                          "finds": [public(f) for f in items]})
    return {"id": job["id"], "name": job["name"], "words": job["words"], "types": types,
            "count": len(finds), "notes": list(job.get("notes") or [])}


def search_jobs(jobs: list[dict], *, kinds: Any = None,
                progress: Callable[[str], None] = lambda _l: None,
                per_site: int = SHOW_PER_SITE, subject: Any = "",
                allow_trading: Optional[bool] = None, with_finds: bool = False) -> dict:
    """Search every job on every site (in parallel). Returns
    ``{"jobs": [grouped job], "notes": [str], "count": int}``; finds are kept
    so :func:`recall` (and so ``install_thing``) can use them. *subject* is
    what the bot is for (her goal): a find that shares no subject word with
    it or with the job is left out (:func:`on_subject`). Things that can
    trade money are shown only when *allow_trading* (her own words asked for
    trading); None means the job's words are hers and decide. *with_finds*
    (the build pipeline, :mod:`puffpai.build.find`): the result also has
    ``"finds"``, every find itself."""
    kind_keys = clean_kinds(kinds)
    wanted = sites_for(kind_keys)
    notes: list[str] = []
    asked: list[str] = []
    for site in wanted:
        if _switched_off(site):
            notes.append(W.SITE_OFF.format(site=W.SITE_NAMES[site]))
        else:
            asked.append(site)
    tasks: list[tuple[int, str, int, str]] = [
        (i, s, n, q) for i in range(len(jobs)) for s in asked
        for n, q in enumerate(
            books.queries(jobs[i]["words"], _book_subject(jobs[i], subject),
                          jobs[i].get("books"), jobs[i].get("topic_searches"),
                          _book_phrase(jobs[i], subject))
            if s == books.SITE else queries_for(s, jobs[i]["words"]))]
    answers: dict[tuple[int, str], dict[int, list[dict]]] = {}
    results: dict[tuple[int, str], list[dict]] = {}
    subject_words = key_words(subject, limit=6)
    quiet: set[str] = set()
    for job in jobs:
        progress(W.LOOKING_LINE.format(words=f"“{job['words']}”",
                                       sites=", ".join(W.SITE_NAMES[s] for s in asked)
                                       or "nowhere"))
    # The same search for two jobs (the bot's subject, asked of the
    # libraries for every job) is sent once.
    asks: dict[tuple[str, str], list[tuple[int, int]]] = {}
    for i, site, n, query in tasks:
        asks.setdefault((site, query), []).append((i, n))
    # Each library is asked at most once a second and waits a few seconds
    # for its answer, so the books' later searches (each job's topics) go in
    # small waves after the subject's own search has had the libraries to
    # itself.
    waves = [[k for k, v in asks.items() if k[0] != books.SITE or min(n for _i, n in v) == 0]]
    later = sorted((k for k, v in asks.items() if k[0] == books.SITE
                    and min(n for _i, n in v) > 0),
                   key=lambda k: min(n for _i, n in asks[k]))
    waves += [later[n:n + BOOK_WAVE] for n in range(0, len(later), BOOK_WAVE)]

    def run(wave: list[tuple[str, str]]) -> None:
        with cf.ThreadPoolExecutor(max_workers=min(WORKERS, len(wave))) as pool:
            futs = {pool.submit(_one_site, site, query, max(per_site * 2, 6)):
                    (site, query) for site, query in wave}
            for fut in cf.as_completed(futs):
                site, query = futs[fut]
                _site, found, failed = fut.result()
                for i, n in asks[(site, query)]:
                    answers.setdefault((i, site), {})[n] = [dict(f) for f in found]
                    if failed and n == 0:
                        quiet.add(site)

    for wave in waves:
        if wave:
            run(wave)
    # A book search of two words that found no book ("canine health") is
    # asked once more with its most telling word ("canine"), screened again.
    again: list[tuple[str, str]] = []
    for (site, query), who in list(asks.items()):
        if site != books.SITE or len(query.split()) < 2:
            continue
        i0, n0 = who[0]
        if any(f.get("tier") != "wikipedia_topic"
               for f in answers.get((i0, site), {}).get(n0, [])):
            continue
        word = books.retry_search(query, _book_subject(jobs[i0], subject), jobs[i0]["words"],
                                  _book_phrase(jobs[i0], subject) or "")
        if not word or (site, word) in asks:
            continue
        asks[(site, word)] = [(i, n + RETRY_OFFSET) for i, n in who]
        again.append((site, word))
    for n in range(0, len(again), BOOK_WAVE):
        run(again[n:n + BOOK_WAVE])
    for (i, site), by_query in answers.items():
        wanted = key_words(jobs[i]["words"], limit=6) + subject_words
        found = _merge([by_query[n] for n in sorted(by_query)])
        found = [f for f in found if not kind_keys or f["type"] in kind_keys]
        if site == books.SITE:
            results[(i, site)] = found     # picked per job below
        else:
            results[(i, site)] = [f for f in found if site in MEANING_SITES
                                  or on_subject(f, wanted)][:per_site]
    for site in sorted(quiet, key=ALL_SITES.index):
        notes.append(W.SITE_QUIET.format(site=W.SITE_NAMES[site]))
    seen: set[str] = set()
    grouped: list[dict] = []
    every: list[dict] = []
    topic_book = False
    for i, job in enumerate(jobs):
        mine: list[dict] = []
        trading = (bool(W.ASKS_TRADING_RE.search(job["words"])) if allow_trading is None
                   else bool(allow_trading))
        left_out = 0
        for site in asked:   # the shelves first, then the code sites, each in its own order
            found = results.get((i, site), [])
            if site == books.SITE:
                # Books about the bot's subject, closest in meaning first,
                # from those no earlier job shows (the subject's own search
                # answers every job).
                found = books.for_job(found, _book_subject(job, subject), job["words"],
                                      shown=seen, topics=job.get("topics"),
                                      phrase=_book_phrase(job, subject))
            for f in found:
                if f["id"] in seen or _same_clawhub_skill(f["id"], seen):
                    continue
                if f.get("can_trade") and not trading:
                    left_out += 1     # never offered by default (her rule)
                    continue
                if f.get("tier") == "wikipedia_topic":
                    if topic_book:    # one Wikipedia book for the whole bot
                        continue
                    topic_book = True
                seen.add(f["id"])
                if f.get("type") == "book":
                    seen.add(books.title_key(f))    # its copy in another library too
                f["job"] = job["id"]
                if job.get("cap"):
                    f["cap"] = job["cap"]
                mine.append(f)
        every.extend(mine)
        if not mine:
            job.setdefault("notes", []).append(W.NOTHING_FOUND)
        if left_out:
            job.setdefault("notes", []).append(W.TRADE_LEFT_OUT.format(
                n=left_out, verb="was" if left_out == 1 else "were"))
        grouped.append((job, mine))
    _caps_by_meaning(every)
    _add_verdicts(every)
    books.pick_ticked(every)
    remember(every)
    out_jobs = [_group(job, mine) for job, mine in grouped]
    progress(W.DONE_LOOKING + f" {len(every)} found.")
    out = {"jobs": out_jobs, "notes": notes, "count": len(every)}
    if with_finds:
        out["finds"] = every
    return out


def _caps_by_meaning(finds: list[dict]) -> None:
    """Finds not searched for one capability (the whole-bot search, her own
    words) are tagged with the capabilities they are close to in meaning, so
    installing one covers them (``f["cap"]`` the closest, ``f["caps"]`` all)."""
    todo = [f for f in finds if not f.get("cap")]
    if not todo:
        return
    from puffpai.finding import capabilities
    caps = capabilities.load()
    if not caps:
        return
    for f, ids in zip(todo, capabilities.match([capabilities.find_text(f) for f in todo],
                                               caps)):
        if ids:
            f["cap"], f["caps"] = ids[0], ids


def _book_subject(job: dict, subject: Any) -> Any:
    """The subject a job's books must be about: the bot's, unless the job is
    her own search for something else ("knitting" in a wedding bot's chat)."""
    if job.get("own") and not books.shares_subject(job["words"], subject):
        return ""
    return subject


def _book_phrase(job: dict, subject: Any) -> Optional[str]:
    """The bot's subject for books (``book_subject``, set with the job's
    topics), unless the job is her own search for something else."""
    if not _book_subject(job, subject):
        return None
    return job.get("book_subject") or None


def _same_clawhub_skill(fid: str, seen: set[str]) -> bool:
    """The Hermes list names ClawHub skills without their owner: such a find is
    the same skill as a ClawHub find already shown with that name."""
    if not fid.startswith("clawhub:") or "/" in fid:
        return False
    slug = fid.split(":", 1)[1]
    return any(s.startswith("clawhub:") and s.rsplit("/", 1)[-1] == slug for s in seen)


def _job(i: int, name: str, words: str) -> dict:
    return {"id": f"j{i}", "name": name[:80], "words": " ".join(words.split())[:80]}


def find_things(words: Any, kinds: Any = None, *,
                progress: Callable[[str], None] = lambda _l: None,
                subject: Any = "", allow_trading: Optional[bool] = None,
                cap: Any = "") -> dict:
    """Search for *words* everywhere (or only for the *kinds* asked).
    *subject* is what the bot is for, if known. When the words are not hers
    (the chat model wrote them), *allow_trading* says whether she asked.
    *cap*: one of the bot's capabilities; its finds are tagged with it."""
    text = " ".join(str(words or "").split())[:100]
    if not text:
        return {"jobs": [], "notes": ["Say what to look for."], "count": 0}
    job = dict(_job(1, text, text), own=True)
    if cap:
        # "Find something for this" on one of the bot's capabilities.
        from puffpai.finding import capabilities
        if capabilities.get(cap) is not None:
            job["cap"] = str(cap)
    return search_jobs([job], kinds=kinds, progress=progress,
                       subject=subject, allow_trading=allow_trading)


# ── from a goal sentence ────────────────────────────────────────────────────

def _brain(cfg: Any = None) -> tuple[str, str]:
    try:
        from puffpai.wizard import needs
        if cfg is None:
            from puffpai.config import get_config
            cfg = get_config()
        return needs._brain(cfg)  # noqa: SLF001 - the same pick the wizard uses
    except Exception:  # noqa: BLE001
        return "", ""


def jobs_for_goal(goal: str, *, cfg: Any = None, asker: Optional[Callable] = None,
                  deadline_s: float = 90.0) -> tuple[list[dict], str]:
    """``(jobs, line)`` for a goal: the whole bot first, then its main jobs,
    each with short search words the brain picked (never personal details:
    the needs prompt forbids them)."""
    from puffpai.wizard import needs_llm
    url, model = _brain(cfg)
    answer = None
    if asker is not None or (url and model):
        try:
            ask = asker or (lambda g: needs_llm.ask(g, url=url, model=model,
                                                    prompt=needs_llm.PROMPT_V5,
                                                    deadline_s=deadline_s))
            answer = ask(goal)
        except Exception as exc:  # noqa: BLE001 - a brain that fails decides nothing
            logger.info("finding: jobs not read (%s)", type(exc).__name__)
            answer = None
    jobs: list[dict] = []
    if isinstance(answer, dict) and answer.get("jobs"):
        kind = " ".join(str(answer.get("kind") or "").split())
        kind = re.sub(r"(?i)\s+bots?$", "", kind)
        if kind:
            # Its books are the bot's subject itself, searched for every job.
            jobs.append(dict(_job(0, f"Everything for a {kind} bot", kind), books=[],
                             whole=True))
        later: list[dict] = []
        for j in answer["jobs"]:
            words = ""
            for part in j.get("parts") or []:
                if part.get("search"):
                    words = part["search"][0]
                    break
            words = words or str(j.get("name") or "").lower()
            if not words:
                continue
            job = _job(len(jobs) + len(later), str(j.get("name") or words), words)
            job["does"] = " ".join(str(j.get("does") or "").split())[:160]
            if len(jobs) < MAX_JOBS and all(words != x["words"] for x in jobs):
                # The brain's own searches for books and guides on this job.
                job["books"] = [str(w)[:80] for p in j.get("parts") or []
                                if p.get("kind") == "knowledge"
                                for w in p.get("search") or []][:2]
                jobs.append(job)
            else:
                # Still one of the bot's jobs (a capability): not searched
                # now, but its "Find something for this" searches it later.
                later.append(dict(job, later=True))
        names = ", ".join(j["name"] for j in jobs)
        return jobs + later, W.JOBS_LINE.format(jobs=names)
    words = ""
    try:
        from puffpai.wizard import service
        words = service.early_kind(goal)
    except Exception:  # noqa: BLE001
        words = ""
    if not words:
        # "a bot to help me train my new puppy" is a puppy bot.
        from puffpai.catalogs import goal_words
        words = goal_words.plain_subject(goal)
    if not words:
        words = " ".join(re.findall(r"[A-Za-z0-9][A-Za-z0-9'-]*", goal)[:4]).lower()
    return [dict(_job(0, f"Everything for a {words} bot", words), whole=True)], W.NO_BRAIN_LINE


def find_for_goal(goal: Any, *, kinds: Any = None, cfg: Any = None,
                  progress: Callable[[str], None] = lambda _l: None,
                  asker: Optional[Callable] = None, keep: bool = False) -> dict:
    """Jobs from the goal, then :func:`search_jobs`. The Hermes list starts
    loading while the brain thinks. *keep* (the wizard's own search): the
    jobs are kept as the bot's capabilities and each find is tagged with
    the capability it was found for (:mod:`puffpai.finding.capabilities`)."""
    goal = " ".join(str(goal or "").split())[:400]
    if not goal:
        return {"jobs": [], "notes": ["Say what your bot is for."], "count": 0}
    preload = None
    if "hermes" in sites_for(clean_kinds(kinds)) and not _switched_off("hermes"):
        preload = threading.Thread(target=_preload_hermes, daemon=True)
        preload.start()
    progress("Working out your bot's jobs.")
    jobs, line = jobs_for_goal(goal, cfg=cfg, asker=asker)
    if keep:
        from puffpai.finding import capabilities
        capabilities.save(goal, jobs)
    jobs = [j for j in jobs if not j.get("later")]
    progress(line)
    if books.SITE in sites_for(clean_kinds(kinds)) and not books.off() \
            and not _switched_off(books.SITE):
        # The brain suggests a few book topics per job ("etiquette" for a
        # wedding, "troubleshooting" for 3D printing); the job's own words
        # when it cannot.
        url, model = _brain(cfg) if books.TOPIC_ASKER is None else ("", "")
        books.topics_for_jobs(goal, jobs, url=url, model=model)
    if preload is not None:
        preload.join(timeout=120)
    # The jobs' words are the model's: only her goal sentence can ask for trading.
    result = search_jobs(jobs, kinds=kinds, progress=progress, subject=goal,
                         allow_trading=bool(W.ASKS_TRADING_RE.search(goal)))
    result["jobs_line"] = line
    return result


# ── from a pasted link ──────────────────────────────────────────────────────

#: Web addresses whose page names one project on a site the search already
#: asks: the host -> the site.
LINK_SITES = {"github.com": "github", "www.github.com": "github",
              "codeberg.org": "codeberg", "www.npmjs.com": "npm", "npmjs.com": "npm",
              "pypi.org": "pypi", sites.CLAWHUB: "clawhub", sites.SKILLS_SH: "skills.sh"}
_LINK_RE = re.compile(r"^https?://\S+$", re.I)
_PART_RE = re.compile(r"^[A-Za-z0-9@][A-Za-z0-9._@-]{0,99}$")


def is_link(text: Any) -> bool:
    return bool(_LINK_RE.match(str(text or "").strip()))


def _norm_url(url: Any) -> str:
    text = str(url or "").strip().lower().split("#")[0].split("?")[0].rstrip("/")
    text = re.sub(r"^https?://(www\.)?", "", text)
    return text[:-4] if text.endswith(".git") else text


def link_search(url: Any) -> Optional[tuple[str, str]]:
    """``(site, what to ask it)`` for a link to one project, or None."""
    from urllib.parse import urlsplit
    try:
        parts = urlsplit(str(url or "").strip())
    except ValueError:
        return None
    site = LINK_SITES.get((parts.hostname or "").lower())
    path = [p for p in parts.path.split("/") if p]
    if not site or not path or not all(_PART_RE.match(p) for p in path[:3]):
        return None
    if site == "github" and len(path) >= 2:
        repo = path[1][:-4] if path[1].endswith(".git") else path[1]
        return site, f"repo:{path[0]}/{repo}"
    if site == "codeberg" and len(path) >= 2:
        return site, path[1]
    if site == "npm" and len(path) >= 2 and path[0] == "package":
        name = "/".join(path[1:3]) if path[1].startswith("@") and len(path) >= 3 else path[1]
        return site, name
    if site == "pypi" and len(path) >= 2 and path[0] == "project":
        return site, path[1]
    if site in ("clawhub", "skills.sh"):
        return site, path[-1].replace("-", " ")
    return None


def find_from_link(url: Any, *, progress: Callable[[str], None] = lambda _l: None) -> dict:
    """The one project a pasted link names, from the site it is on, ready for
    the same Add, check and yes as any other find. Close matches when the
    site has nothing at that exact address."""
    text = str(url or "").strip()[:300]
    asked = link_search(text)
    job = dict(_job(1, text, text), own=True)
    if asked is None:
        job["notes"] = [W.LINK_UNKNOWN]
        return {"jobs": [_group(job, [])], "notes": [], "count": 0}
    site, query = asked
    if _switched_off(site):
        job["notes"] = [W.SITE_OFF.format(site=W.SITE_NAMES[site])]
        return {"jobs": [_group(job, [])], "notes": [], "count": 0}
    progress(W.LOOKING_LINE.format(words=f"“{text}”", sites=W.SITE_NAMES[site]))
    _site, found, failed = _one_site(site, query, SHOW_PER_SITE * 2)
    want = _norm_url(text)

    def _same(f: dict) -> bool:
        # The project's own page, or a page inside it (".../tree/main").
        have = _norm_url(f.get("url"))
        return bool(have) and (want == have or want.startswith(have + "/"))

    exact = [f for f in found if _same(f)]
    finds = exact or found[:SHOW_PER_SITE]
    notes: list[str] = []
    if failed:
        notes.append(W.SITE_QUIET.format(site=W.SITE_NAMES[site]))
    elif not finds:
        job["notes"] = [W.NOTHING_FOUND]
    elif not exact:
        job["notes"] = [W.LINK_CLOSE]
    for f in finds:
        f["job"] = job["id"]
    _add_verdicts(finds)
    remember(finds)
    progress(W.DONE_LOOKING + f" {len(finds)} found.")
    return {"jobs": [_group(job, finds)], "notes": notes, "count": len(finds)}


def _preload_hermes() -> None:
    try:
        hermes_index.load()
    except Exception:  # noqa: BLE001
        pass


#: How many finds of one type the model is shown for one job.
TEXT_PER_TYPE = 4


def as_text(result: dict, max_lines: int = 40) -> str:
    """The find list as plain text, for the model (and for a terminal),
    grouped by type so the apps and code are as plain to see as the skills."""
    out: list[str] = []
    n = 0
    types_seen: set[str] = set()
    for job in result.get("jobs") or []:
        out.append(f"For {job['name']}:")
        for t in job.get("types") or []:
            shown = [f for f in t["finds"]][:TEXT_PER_TYPE]
            if not shown or n >= max_lines:
                continue
            types_seen.add(t["type"])
            out.append(f"  {t['type_words']}:")
            for f in shown:
                if n >= max_lines:
                    break
                n += 1
                out.append(f"  - [{f['id']}] {f['line']}")
        for note in job.get("notes") or []:
            out.append(f"  {note}")
    for note in result.get("notes") or []:
        out.append(note)
    if n:
        if types_seen - {"skill"}:
            out.append("Tell the owner about the skills AND the apps, tool servers and code "
                       "above, each with its type, so she can pick.")
        out.append("To install one, call install_thing with its id in square brackets, "
                   "after telling the person what it is and what it runs.")
    return "\n".join(out) if out else W.NOTHING_FOUND


__all__ = ["ALL_SITES", "SHOW_PER_SITE", "as_text", "clean_kinds", "find_for_goal",
           "find_from_link", "find_things", "is_link", "link_search", "jobs_for_goal", "key_words", "on_subject", "public", "recall", "remember", "search_jobs",
           "sites_for"]
