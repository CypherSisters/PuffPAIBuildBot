"""From jobs to plan lines: where to look, trust, relevance.

``plan_items(needs, ...)`` walks her ticked jobs in order (asked, then usual,
then her own), and for each job part it can serve:

1. finds on this computer first (``where_to_look.find_local``: snapshots,
   nothing sent): code and app lists, live-info lists, the book lists;
2. when the build is online and she pressed **Start looking**
   (``allow_send``), ``where_to_look.find_online`` with only that job's
   ``Sendable`` phrases (from :func:`puffpai.wizard.needs.sends`) and, once
   per build, the bot's subject;
3. the trust floor, relevance stage 1, a closer look at the best finds of
   every job (``where_to_look.details``: README feature lines, licence file,
   pinned version), relevance stage 2 (:mod:`puffpai.catalogs.meaning`);
4. plan lines grouped by job (``need``): apps and code, live cards and books
   (through BuildBot's existing live-card and book appliers), pictures; notes
   for things BuildBot cannot add yet, a count of what was left out, every job
   it did not reach in time, and "Similar projects also have".

Every find, whatever list it came from, is matched to the jobs by meaning
(never by a shared word), so a live card or a book is offered only when what
it does fits a job. Books are offered only for "learn about" jobs.

With no jobs at all (the needs step had no brain and found no list in her
words), the plan keeps the live cards and books BuildBot offered before round
3, from the same providers as before.

Searching stops after :data:`SEARCH_DEADLINE_S`. Results are memoised by
(needs digest, online, allow_send), so redrawing the plan never searches
again. The server-side record of every line is kept here and handed to the
appliers by :func:`found_for`; nothing the browser sends back is ever read.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import contextlib
import copy
import hashlib
import importlib
import json
import re
import threading
import time
import unicodedata
from collections import Counter
from typing import Any, Callable, Optional

from puffpai.catalogs import meaning

SEARCH_DEADLINE_S = 90.0
#: Of the whole time, this much is kept for closer looks and scoring.
CLOSER_LOOK_S = 30.0
#: No closer look asks anything this long before the end; scoring and the
#: brain's check follow. Measured with README lines read during the looks and
#: the brain warmed after the searches: the plan is ready 64 to 77 s after the
#: start, so looks run until 65 s (they stopped at 57 s before).
SCORING_S = 25.0
#: Closer looks at once. A small project is read as one archive from its own
#: host (paced per host), so more looks than two fit inside the search time.
CLOSER_WORKERS = 6
#: Measured: 80 to 88.5 s of 90 s, the slowest 1.5 s under, so with two jobs
#: or more, closer looks end this long before the end instead. What follows
#: the looks is not cut by the clock: looks already under way finish (about
#: 3 s), both scorings run (measured live on a shared, busy brain: 6 s and
#: 13 s) and the lines are built (about 6 s), 28 s in all after the looks were told to end. 31 s left that
#: run at 87.2 s; 38 s keeps about 10 s in hand. A one-job build reads fewer
#: finds, its looks were done by about 35 s, and it keeps SCORING_S.
SCORING_MULTI_S = 42.0
#: Measured: one goal took 91.6 s, then 87.0 s, another 91.6 s, so the plan
#: aims to be ready this long after the start, a real margin under
#: SEARCH_DEADLINE_S. The brain's checks stop by the aim, not
#: by the deadline (they could run to 84 s before), and the looks of a build of
#: two jobs or more end 4 s earlier (SCORING_MULTI_S 38 -> 42). SEARCH_DEADLINE_S
#: stays the limit nothing may pass.
PLAN_AIM_S = 78.0


def _look_until(deadline: float, jobs: list) -> float:
    """When closer looks end: SCORING_S before *deadline* for one job,
    SCORING_MULTI_S before it for two jobs or more."""
    return deadline - (SCORING_S if len(jobs or ()) <= 1 else SCORING_MULTI_S)


#: Without this, 42 to 167 fitting finds per failing goal were left out as
#: "no open licence" because GitHub reported none. The
#: best fitting of them per job are read at the pin after every other closer
#: look, in the time left: at most this many per job, and this many in all.
UNREPORTED_LOOKS_PER_JOB = 2
UNREPORTED_LOOKS_MAX = 10
_NOT_LOOKED = ("page", "reads like orders", "far", "not for these jobs", "no jobs")


def _unreported_licence_looks(scored: list, found: dict, *, done: set) -> list[dict]:
    """GitHub finds that fit a job, whose licence GitHub reports as none, worth
    reading at the pin: the top :data:`UNREPORTED_LOOKS_PER_JOB` per job by
    fit, taken in turns, at most :data:`UNREPORTED_LOOKS_MAX`.

    Only a project small enough to read as one archive: the archive lists
    every file at the top, so a licence file GitHub did not recognise is read
    whole by the plan's own reader. Read file by file, a project GitHub says
    has no licence gets no licence read (github.read_licence), so that look
    could not help. A licence is only ever read, never guessed: a find with no
    licence file still has none. Never one already looked at, archived, a
    fork, or one no look could carry past the trust floor."""
    gh = _optional("puffpai.catalogs.searchers.github")
    small = getattr(gh, "small_enough", None) if gh is not None else None
    if not callable(small):
        return []
    by_job: dict[str, list[tuple]] = {}
    for f in scored or ():
        fid = str(f.get("id"))
        rec = found.get(fid) if isinstance(found, dict) else None
        m = f.get("meaning") or {}
        repo = rec.get("repo") if isinstance(rec, dict) and isinstance(rec.get("repo"), dict) else {}
        if (rec is None or fid in done or rec.get("source") != "github"
                or rec.get("part") not in CODE_PARTS or repo.get("host") != "github.com"
                or "license_reported" not in repo or repo.get("license_reported") is not None
                or repo.get("license_read") or repo.get("archived") or repo.get("fork")
                or not small(repo) or m.get("why_dropped") in _NOT_LOOKED
                or not m.get("job") or meaning.is_catalog_row(rec)
                or (m.get("blurb") or 0.0) < meaning.LOOK_MIN_BLURB
                or meaning.trust_hopeless(rec)):
            continue
        by_job.setdefault(str(m["job"]), []).append((-(m.get("fit") or m.get("blurb") or 0.0),
                                                     fid, rec))
    lists = [sorted(rows, key=lambda t: (t[0], t[1]))[:UNREPORTED_LOOKS_PER_JOB]
             for _job, rows in sorted(by_job.items(), key=lambda kv: (len(kv[0]), kv[0]))]
    picked: list[dict] = []
    for turn in range(UNREPORTED_LOOKS_PER_JOB):
        for rows in lists:
            if turn < len(rows):
                picked.append(rows[turn][2])
    return picked[:UNREPORTED_LOOKS_MAX]


CODE_PARTS = ("code-app", "data-template")
MAX_LABEL = 80
MAX_WHAT = 120
#: The plan keeps this many characters of a line's detail
#: (``proposal.MAX_DETAIL``).
MAX_DETAIL = 300
MAX_NOTE_NAME = 60
_ID_CHARS = re.compile(r"[^A-Za-z0-9_.:/-]")
#: Evidence clusters: a feature line fits a cluster at this cosine or more.
SIMILAR_CLUSTER = 0.50
SIMILAR_MIN_PROJECTS = 2
SIMILAR_MAX = 5
#: At most this many book lines on one plan (best fit first).
MAX_BOOKS = 4

# -- owner-facing lines (also in tests/fixtures/ui_strings/needs_and_find_parts.txt) ----------
NOTHING_FOUND = ("BuildBot found nothing ready-made it can add for this. Your bot's brain can "
                 "still help with it in chat.")
NOT_IN_BUILD = "Looking for apps and code is not in this build yet."
LINE_LOCAL = "On this computer: {n} found"
LINE_CHECKING = "Checking what fits your jobs ... {kept} kept, {left} left out"
LINE_STOPPED = "Stopped looking to keep this quick."
#: One place's own line while the searches run (otherwise the page says
#: only "On this computer: 144 found" for 75 seconds).
LINE_SOURCE = "{place}: {n} found"
LINE_NOTHING = "{place}: nothing found"
LINE_CHECKING_START = "Checking what fits your jobs ..."
LINE_GITHUB_SPENT = ("BuildBot has used the GitHub searches it may make for now, so it keeps "
                     "looking on the other sites.")
#: A place's line is said again at most this often while it finds more (the
#: page shows only its latest count, service._latest_lines).
SOURCE_LINE_EVERY_S = 8.0
#: The name a person reads for each place BuildBot looks.
PLACE_NAMES = {"github": "GitHub", "codeberg": "Codeberg", "gitlab": "GitLab", "npm": "npm",
               "pypi": "PyPI", "huggingface": "Hugging Face", "commons": "Wikimedia Commons",
               "openverse": "Openverse", "wikipedia": "Wikipedia", "wikibooks": "Wikibooks",
               "mcp": "the list of tools for AI apps", "hacs": "the Home Assistant community list",
               "awesome_selfhosted": "the list of apps people run at home",
               "gutenberg": "Project Gutenberg", "openstax": "OpenStax", "kiwix": "Kiwix",
               "seeds": "BuildBot's list of live info", "public_apis": "the list of free websites",
               "apis_guru": "the list of free websites"}


def place_name(source: str) -> str:
    return PLACE_NAMES.get(str(source or ""), "Other places")


#: What each place a search asks shares, in plain words (not "Hugging Face
#: datasets and npm" on the needs step).
_PLACE_KINDS = (("free apps and code", ("GitHub", "Codeberg", "GitLab", "npm", "PyPI")),
                ("free pictures", ("Wikimedia Commons", "Openverse")),
                ("free data", ("Hugging Face datasets", "Hugging Face")),
                ("free articles", ("Wikipedia", "Wikibooks")))
_PLACE_SHORT = {"Hugging Face datasets": "Hugging Face"}


def _and(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def plain_places(labels: list[str]) -> str:
    """"sites where people share free apps and code (GitHub and npm) and free
    pictures (Openverse)" for the places a search asks; a place not named
    here keeps its own name."""
    labels = [str(x) for x in labels or () if str(x).strip()]
    groups, rest = [], [x for x in labels]
    for what, names in _PLACE_KINDS:
        got = [x for x in labels if x in names]
        if got:
            groups.append(f"{what} ({_and([_PLACE_SHORT.get(x, x) for x in got])})")
            rest = [x for x in rest if x not in got]
    if not groups:
        return _and(labels) if labels else ""
    said = "sites where people share " + _and(groups)
    return _and([said] + rest)


#: With no jobs there is nothing to check a find against: the lines come from
#: BuildBot's own lists for the goal, and are never called a fit for "your jobs".
LINE_NO_JOBS = "No jobs to check against, so these come from BuildBot's own lists: {n} found"
#: On the top unticked find of a job with nothing ticked. It never ticks it.
BEST_MATCH = "Best match for this job."
BEST_SECTIONS = ("apps_code", "live_info", "knowledge", "pictures")
FACT_STARS = "{stars} stars, last changed {when}."
FACT_FEW = "Few people use it yet ({stars} {word}). Last changed {when}."
#: Sizes read the same as the plan's own total ("547 kB", cards.format_bytes).
FACT_SIZE = "About {size}."
# Never "cannot reach the internet"; only its web requests are stopped
MODE_STATIC = "Opens inside your bot. Its web requests to the internet are stopped unless you say yes."
MODE_BUILD = "BuildBot builds it in the locked box first (about a minute)."
MODE_CLI = "Runs in the locked box. Your bot can use it on files in a folder you choose."
MODE_DATA = "As a list your bot can read."
WHY = "helps with: {job}"
NOTE_LINE = "{name} ({where}, {reason})"
NOTE_LINE_NO_WHERE = "{name} ({reason})"
OWN_PICTURES = 'Use your own pictures for "{job}"'
OWN_PICTURES_DETAIL = "You choose a folder after the build. BuildBot only reads it."
PALETTE = "Colours and themes from your pictures (BuildBot made this for you)"
PALETTE_DETAIL = ("Reads the pictures in the folder you choose and pulls out colour sets and "
                  "themes.")
STOCK_PICTURES_DETAIL = "Free to use with credit; the credits are kept with each picture."
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December")


def _optional(modname: str):
    try:
        return importlib.import_module(modname)
    except ModuleNotFoundError as e:
        if e.name is not None and (e.name == modname or modname.startswith(e.name + ".")):
            return None          # not in this build yet
        raise                    # exists but broken: fail loudly


_MEMO: dict[tuple, dict] = {}
_FOUND: dict[str, dict] = {}
_EVIDENCE: dict[tuple, dict] = {}
_LAST: list[tuple] = []
_LOCK = threading.Lock()


def _digest(needs: dict) -> str:
    return hashlib.sha256(json.dumps(needs, sort_keys=True, default=str).encode("utf-8")
                          ).hexdigest()


def found_for(item_id: str) -> Optional[dict]:
    """The server-side ``found/v1`` record behind a plan line (appliers use only this)."""
    with _LOCK:
        rec = _FOUND.get(str(item_id or ""))
    return copy.deepcopy(rec) if rec is not None else None


def last_evidence() -> Optional[dict]:
    """What the last :func:`plan_items` search saw: every raw find with its
    scores, its state and why it was left out (for the acceptance harness and
    for review; it holds no goal text). ``None`` before any search."""
    with _LOCK:
        if not _LAST:
            return None
        return copy.deepcopy(_EVIDENCE.get(_LAST[-1]))


# -- wording ----------------------------------------------------------------------
_ORDER = {"asked": 0, "usual": 1, "owner": 2, "evidence": 3}


def _jobs_in_order(needs: dict) -> list[dict]:
    jobs = [j for j in needs.get("jobs") or [] if j.get("ticked")]
    return sorted(jobs, key=lambda j: _ORDER.get(str(j.get("source")), 4))


def _where(found: dict) -> str:
    repo = found.get("repo") or {}
    if isinstance(repo, dict) and repo.get("owner") and repo.get("name"):
        return f"{repo.get('host') or 'github.com'}/{repo['owner']}/{repo['name']}"
    url = str(found.get("url") or "")
    return re.sub(r"^https?://", "", url).rstrip("/")


def _when(iso: Any) -> tuple[str, str]:
    m = re.match(r"^(\d{4})-(\d{2})", str(iso or ""))
    if not m or not 1 <= int(m.group(2)) <= 12:
        return "", ""
    return f"{m.group(1)}-{m.group(2)}", f"{MONTHS[int(m.group(2)) - 1]} {m.group(1)}"


def _clean(text: Any, limit: int) -> str:
    """One plain line of at most *limit* characters, never cut inside a word."""
    from puffpai.artifact_review import neutralise_label
    text = neutralise_label(" ".join(str(text or "").split()), limit=max(limit * 4, 400))
    # Owner-facing words never carry a long dash, even from a project's own text.
    text = re.sub(r"\s*[—–]\s*", ", ", text).replace("�", "")
    # Nor picture symbols from a project's name ("AI Wedding Planner" plus two emoji).
    text = " ".join("".join(ch for ch in text if unicodedata.category(ch) not in ("So", "Cs", "Co")
                            and ch not in "\u200d\ufe0f").split())
    if len(text) <= limit:
        return text
    cut = text[:limit + 1]
    at = cut.rfind(" ")
    cut = cut[:at] if at >= max(8, limit // 3) else text[:limit]
    return cut.rstrip(" ,;:-(")


def _sentence(text: Any, limit: int) -> str:
    """Like :func:`_clean`, ending with a full stop."""
    out = _clean(text, limit - 1).rstrip(" .,;:-")
    if not out:
        return ""
    return out if out.endswith(("!", "?")) else out + "."


def _size(found: dict) -> Optional[int]:
    repo = found.get("repo") or {}
    kb = repo.get("size_kb") if isinstance(repo, dict) else None
    if isinstance(kb, int) and not isinstance(kb, bool) and kb > 0:
        return kb * 1024
    # A page's own script bytes (details.code_bytes, 0 for a
    # listed find never measured) are not the project's size; a whole app
    # (myfin, chartdb) showed "About 1 KB". A host's 0 is an empty project or
    # one not measured yet, never "About 1 KB" either. No size: none said.
    return None


def _size_words(n: Optional[int]) -> str:
    if n is None:
        return ""
    from puffpai.wizard import cards
    size = cards.format_bytes(n)
    return FACT_SIZE.format(size=size) if size else ""


def _licence_words(found: dict) -> tuple[str, str]:
    lic = meaning._licence(found)
    wtl = _optional("puffpai.catalogs.where_to_look")
    words = ""
    if wtl is not None and lic:
        try:
            words = str(wtl.license_words(lic) or "")
        except Exception:  # noqa: BLE001
            words = ""
    tf = _optional("puffpai.catalogs.trust_floor")
    to_check = getattr(tf, "licence_to_check", None) if tf is not None else None
    if words and callable(to_check) and to_check(found):
        why_of = getattr(tf, "licence_check_words", None)   # say what to check
        why = str(why_of(found) or "") if callable(why_of) else ""
        words = words.rstrip(".") + ", check this licence yourself" + (f": {why}." if why else ".")
    return lic, words


def _apply_kind(mode: str) -> str:
    return {"data_template": "data_import", "cli_tool": "cli_tool"}.get(mode, "code_project")


def _host(found: dict) -> str:
    return {"codeberg": "codeberg.org"}.get(str(found.get("source")), "codeload.github.com")


def _item_id(found: dict, section: str = "apps_code") -> str:
    raw = _ID_CHARS.sub("-", str(found.get("id") or ""))
    return (section + ":" + raw)[:160] if raw else ""


def _extras(item: dict, found: dict, job: dict) -> dict:
    m = found.get("meaning") or {}
    item.update({"need": job["id"], "why": WHY.format(job=job.get("name") or ""),
                 "might_help": m.get("state") == "might_help"})
    return item


def _names_it(friendly: str, found: dict) -> bool:
    """Does the friendly name share a word with the project's own name or summary?"""
    words = set(re.findall(r"[a-z0-9]{3,}", friendly.lower()))
    if not words:
        return False
    repo = found.get("repo") if isinstance(found.get("repo"), dict) else {}
    own = " ".join(str(x or "") for x in (found.get("name"), repo.get("name"),
                                          found.get("what_it_is")))
    own = re.sub(r"([a-z])([A-Z])", r"\1 \2", own).lower()
    return bool(words & set(re.findall(r"[a-z0-9]{3,}", own)))


def _item(found: dict, job: dict) -> dict:
    """One plan line (ProposalItem + extras) for a ticked or might-help code find."""
    m = found["meaning"]
    repo = found.get("repo") or {}
    details = found.get("details") or {}
    mode = str(found.get("mode_guess") or "unknown")
    where = _where(found)
    friendly = _clean(found.get("friendly") or found.get("name"), 60)
    if not _names_it(friendly, found):
        friendly = ""   # a page title like "Windows" that says nothing about it
    label = f"{friendly} ({where})" if friendly and where and \
        friendly.lower() != str(repo.get("name") or "").lower() else (where or friendly)
    if len(label) > MAX_LABEL:
        label = (where or friendly)[:MAX_LABEL]
    changed, when = _when(repo.get("pushed_at") if isinstance(repo, dict) else None)
    stars = repo.get("stars") if isinstance(repo, dict) else None
    trust = found.get("trust") or {}
    lic, lic_words = _licence_words(found)
    size = _size(found)
    facts_line = []
    if isinstance(stars, int) and when:
        if trust.get("path") == "stars" or trust.get("path") == "listed" and stars >= 5:
            facts_line.append(FACT_STARS.format(stars=f"{stars:,}", when=when))
        else:
            facts_line.append(FACT_FEW.format(stars=stars, word="star" if stars == 1 else "stars",
                                              when=when))
    if lic_words:
        facts_line.append(lic_words)
    if size is not None:
        facts_line.append(_size_words(size))
    mode_line = {"static_web": MODE_STATIC, "web_build": MODE_BUILD, "cli_tool": MODE_CLI,
                 "data_template": MODE_DATA}.get(mode, "")
    # The plan keeps MAX_DETAIL characters of a line: the project's own words
    # give way first, so how it opens ("Opens inside your bot") is always
    # there, with room for the best-match words (a long description must not
    # push that sentence out).
    rest = " ".join(x for x in [" ".join(facts_line), mode_line] if x)
    room = MAX_DETAIL - len(BEST_MATCH) - 1 - len(rest) - 1
    what = _sentence(found.get("what_it_is"), min(MAX_WHAT, room)) if room >= 30 else ""
    detail = " ".join(x for x in [what, rest] if x)
    box = mode in ("web_build", "cli_tool")
    hosts = [_host(found)]
    runtime = [str(h) for h in (details.get("runtime_hosts_guess") or [])] \
        if isinstance(details, dict) else []
    item = {
        "id": _item_id(found), "section": "apps_code", "label": label, "detail": detail,
        "size_bytes": size, "outside_host": hosts[0],
        "default_on": m["state"] == "ticked", "needs_yes": True,
        "apply": {"kind": _apply_kind(mode), "ref": str(found.get("id"))},
        "requires": ["box_setup"] if box else [],
        "facts": {"stars": stars, "changed": changed, "license": lic, "mode": mode,
                  "trust_path": trust.get("path") or "", "hosts_all": hosts,
                  "runtime_hosts": runtime, "size_bytes": size, "box": box},
    }
    return _extras(item, found, job)


#: Small words that name nothing ("Clone the repository" shares "the" with
#: any description).
_NAME_SMALL = frozenset("the and for with your you this that from into our app".split())


def _note(found: dict) -> str:
    name = _clean(found.get("friendly") or found.get("name"), MAX_NOTE_NAME)
    plain = " ".join(w for w in name.split() if w.lower() not in _NAME_SMALL)
    if name and not (plain and _names_it(plain, found)):
        # A README line is not the project's name ("Clone the repository"):
        # the project's own name instead.
        name = _clean(found.get("name"), MAX_NOTE_NAME)
    name = name or "A project"
    where = _where(found)
    reason = meaning.note_reason(found)
    if not where:
        return NOTE_LINE_NO_WHERE.format(name=name, reason=reason)
    return NOTE_LINE.format(name=name, where=where, reason=reason)


#: One reason for several finds of one job ("no licence, so BuildBot cannot use them").
NOTE_REASONS_MANY = {
    meaning.NOTE_NO_LICENCE: "no licence, so BuildBot cannot use them",
    meaning.NOTE_CUSTOM_LICENCE: "their licences have terms of their own, so BuildBot cannot "
                                 "use them",
    meaning.NOTE_SERVER: "each needs its own server, not something BuildBot can set up yet",
    meaning.NOTE_LIBRARY: "code libraries, not apps",
    meaning.NOTE_OTHER: "BuildBot cannot add these kinds of things yet",
    meaning.NOTE_NOT_READ: "BuildBot ran out of time to check them",
    meaning.NOTE_LICENCE_NOT_CHECKED: ("BuildBot could not check their licences: it has used the "
                                       "GitHub checks it may make for now"),
}
NOTES_LINE = "{names} ({reason})"


def _notes(rows: list[dict]) -> list[str]:
    """One note line per reason for one job: 13 "no licence" lines were a wall
    of the same words."""
    by_reason: dict[str, list[dict]] = {}
    for f in rows:
        by_reason.setdefault(meaning.note_reason(f), []).append(f)
    out = []
    for reason, group in by_reason.items():
        if len(group) == 1 or reason not in NOTE_REASONS_MANY:
            out += [_note(f) for f in group]
            continue
        names = [(_where(f) or _clean(f.get("friendly") or f.get("name"), MAX_NOTE_NAME))
                 for f in group]
        out.append(NOTES_LINE.format(names=_and([n for n in names if n] or ["Some projects"]),
                                     reason=NOTE_REASONS_MANY[reason]))
    return out


# -- books and pictures (BuildBot's existing appliers) ------------------------------
def _book_items(rows: list[dict], jobs_by_id: dict, online: bool) -> list[dict]:
    """Book lines for the knowledge finds that fit a "learn about" job."""
    if not rows:
        return []
    from puffpai.catalogs import knowledge_resolver as kr
    from puffpai.catalogs import kiwix_opds
    out: list[dict] = []
    for row in rows:
        try:
            cand = kr.candidate_by_id(str(row["id"]))
        except Exception:  # noqa: BLE001 - a book that cannot be read is not offered
            cand = None
        if not cand or not cand.get("fetchable"):
            continue
        size = cand.get("size_bytes")
        if not isinstance(size, int) or size <= 0:
            ceiling = cand.get("size_ceiling")
            size = ceiling if isinstance(ceiling, int) and ceiling > 0 else None
        size_part = f" ({kr.download_words(cand)})"
        name = str(cand.get("name") or row.get("name") or "")
        if len(name) + len(size_part) > MAX_LABEL:
            name = _clean(name, MAX_LABEL - len(size_part) - 3) + "..."
        item = {"id": "knowledge:" + str(cand["id"]), "section": "knowledge",
                "label": name + size_part, "detail": kr._item_detail(cand)[:400],
                "size_bytes": size,
                "outside_host": str(cand.get("host") or "") or kiwix_opds.DOWNLOAD_HOST,
                "default_on": bool(row["meaning"]["state"] == "ticked" and online),
                "needs_yes": True, "apply": {"kind": "knowledge.add", "ref": str(cand["id"])}}
        out.append(_extras(item, row, jobs_by_id[row["meaning"]["job"]]))
    return out


#: Picture sites by the name a person knows (a picture line is named by its
#: title and its site, never by a raw address).
_PICTURE_SITES = (("flickr", "Flickr"), ("wikimedia", "Wikimedia Commons"),
                  ("wikipedia", "Wikipedia"), ("openverse", "Openverse"),
                  ("rawpixel", "rawpixel"), ("nasa", "NASA"), ("smithsonian", "the Smithsonian"))
PICTURE_ON = "{title} (a picture on {site})"
PICTURE_UNTITLED = "A picture on {site}"


def _picture_site(row: dict, host: str) -> str:
    text = f"{host} {row.get('url') or ''} {row.get('source_site') or ''}".lower()
    for key, name in _PICTURE_SITES:
        if key in text:
            return name
    bare = re.sub(r"^(?:www|live|upload|images?|farm\d*)\.", "", str(host or "").lower())
    return bare or "the web"


def _picture_label(row: dict, host: str) -> str:
    title = _clean(row.get("friendly") or row.get("title") or row.get("name"), 60)
    title = " ".join(title.replace("(web address)", " ").split())
    if re.search(r"(?:https?://|www\.|\.(?:com|org|net)/|/\d{5,})", title):
        title = ""            # a raw address is not a title
    site = _picture_site(row, host)
    if not title:
        return PICTURE_UNTITLED.format(site=site)
    label = PICTURE_ON.format(title=title, site=site)
    return label if len(label) <= MAX_LABEL else title


def _picture_item(row: dict, job: dict) -> dict:
    pic = row.get("picture") if isinstance(row.get("picture"), dict) else {}
    host = str(pic.get("host") or "") or re.sub(r"^https?://([^/]+).*$", r"\1",
                                                  str(row.get("url") or "")) or None
    label = _picture_label(row, host or "")
    lic, lic_words = _licence_words(row)
    detail = " ".join(x for x in (_sentence(row.get("what_it_is"), MAX_WHAT),
                                  lic_words or STOCK_PICTURES_DETAIL) if x)
    item = {"id": _item_id(row, "pictures"), "section": "pictures", "label": label or "Pictures",
            "detail": detail, "size_bytes": pic.get("size_bytes") if isinstance(
                pic.get("size_bytes"), int) else None,
            "outside_host": host, "default_on": False, "needs_yes": True,
            "apply": {"kind": "picture_set", "ref": str(row.get("id"))}}
    return _extras(item, row, job)


_PIC_RE = re.compile(r"\b(?:picture|pictures|photo|photos|image|images)\b", re.I)
_LOOK_RE = re.compile(r"\b(?:theme|themes|colou?rs?|mood|moods|style|styles|palette|palettes|"
                      r"look|looks)\b", re.I)


def picture_job(job: dict) -> bool:
    """Is this a job about the look of pictures she has (themes, colours, mood)?"""
    text = f"{job.get('name') or ''} {job.get('does') or ''}"
    return bool(_PIC_RE.search(text) and _LOOK_RE.search(text))


def _own_picture_items(job: dict) -> list[dict]:
    """BuildBot's own two lines for a job about her pictures."""
    name = _clean(job.get("name"), 40)
    common = {"section": "pictures", "size_bytes": None, "outside_host": None,
              "default_on": True, "needs_yes": True, "need": job["id"],
              "why": WHY.format(job=job.get("name") or ""), "might_help": False}
    return [dict(common, id=f"pictures:buildbot:own-pictures-{job['id']}",
                 label=OWN_PICTURES.format(job=name)[:MAX_LABEL], detail=OWN_PICTURES_DETAIL,
                 apply={"kind": "folder_palette", "ref": "folder"}),
            dict(common, id=f"pictures:buildbot:palette-{job['id']}", label=PALETTE,
                 detail=PALETTE_DETAIL, apply={"kind": "folder_palette", "ref": "palette"})]


def _book_rows(job: dict, kind: str) -> list[dict]:
    """Books from BuildBot's own library lists for a "learn about" job, as
    ``found/v1`` rows (read on this computer; nothing is sent). General
    encyclopedias and Wikipedia topic books are left out: a topic book would
    send the job's words to Wikipedia, and an encyclopedia fits any goal."""
    from puffpai.catalogs import goal_words, knowledge_resolver as kr
    text = f"{kind} {job.get('name') or ''}".strip()
    try:
        got = kr.find(text, goal_words.goal_terms(text), live=False, limit=16)
    except Exception:  # noqa: BLE001 - no list, no books
        return []
    out = []
    for cand in got.get("candidates") or []:
        cid = str(cand.get("id") or "")
        if not cid or cid.startswith("wikipedia:") or cand.get("general") \
                or not cand.get("fetchable"):
            continue
        out.append({"schema": "found/v1", "id": cid,
                    "source": cid.split(":", 1)[0] if ":" in cid else "kiwix",
                    "part": "knowledge", "name": str(cand.get("name") or ""),
                    "what_it_is": str(cand.get("what_it_is") or ""),
                    "url": str(cand.get("url") or ""), "mode_guess": "other",
                    "audience": str(cand.get("audience") or ""),
                    "tier": kr.tier_of(cand), "integrable": False,
                    "trust": {"floor_ok": True, "path": "", "pretick_ok": True, "reasons": []}})
    return out


def _at_reader_level(row: dict, reader: Any) -> bool:
    """:func:`meaning.reader_fits`, with the book's audience read from the
    library lists when the find did not bring it (a find from this computer's
    snapshot carries only its id)."""
    if row.get("part") != "knowledge" or "audience" in row and "tier" in row:
        return meaning.reader_fits(row, reader)
    try:
        from puffpai.catalogs import knowledge_resolver as kr
        cand = kr.candidate_by_id(str(row.get("id") or "")) or {}
        full = dict(row, audience=row.get("audience") or cand.get("audience") or "",
                    tier=row.get("tier") or (kr.tier_of(cand) if cand else ""))
    except Exception:  # noqa: BLE001 - an unreadable list: judged by its id
        full = row
    return meaning.reader_fits(full, reader)


#: A find whose own words say it is no longer kept up is never pre-ticked
#: (for example "DEPRECATED) Organized Daily Notes").
_STALE = re.compile(r"\b(?:deprecated|archived|no longer (?:maintained|supported|developed)|"
                    r"unmaintained|abandoned|not maintained)\b", re.I)
#: Outside AI services a find may send her data to (names of services, not
#: topics): such a find is shown, never pre-ticked.
_OUTSIDE_AI = re.compile(
    r"\b(?:openai|chat ?gpt|gpt-?[345]|gpt-?4o|deepseek|anthropic|claude api|gemini api|"
    r"google gemini|mistral ai|groq|openrouter|perplexity api|cohere api|hugging ?face "
    r"inference)\b", re.I)
#: A find made for Home Assistant (an add-on, a card or a dashboard for it).
_HA_ONLY = re.compile(r"\b(?:home ?assistant|hass(?:io)?|lovelace|hacs)\b", re.I)
_DIGIT_WORD = re.compile(r"\d")


def _own_text(row: Optional[dict]) -> str:
    """A find's own words: name, description and the first README lines."""
    row = row or {}
    return " ".join([str(row.get("name") or ""), meaning.what_it_does(row),
                     " ".join(meaning._lines_of(row)[:8])])


def _ha_in_use() -> bool:
    try:
        from puffpai import setup_ops
        return bool(setup_ops.ha_configured())
    except Exception:  # noqa: BLE001 - unknown means not in use
        return False


def _other_kind(text: str, kind: str) -> bool:
    """"3d printer": a find that names a printer but never says 3D is made for
    another kind of printer (a label printer). Only a qualifier with a digit
    in it ("3d", "5e", "4k") marks another kind this way."""
    words = str(kind or "").lower().split()
    if len(words) < 2:
        return False
    head = words[-1]
    quals = [w for w in words[:-1] if _DIGIT_WORD.search(w)]
    if not quals:
        return False
    low = text.lower()
    if not re.search(r"\b%ss?\b" % re.escape(head), low):
        return False
    squeeze = re.sub(r"[\s-]+", "", low)
    return not any(q in squeeze for q in quals)


def tick_doubt(row: Optional[dict], kind: str, *, ha: bool) -> str:
    """Why a find must not be pre-ticked or called the best match ("" when
    nothing): it says it is no longer kept up, it is another kind of the
    thing the bot is about, it is made for Home Assistant when that is not set
    up here, or it sends her data to an outside AI service."""
    text = _own_text(row)
    repo = (row or {}).get("repo") if isinstance((row or {}).get("repo"), dict) else {}
    if repo.get("archived") or _STALE.search(text):
        return "stale"
    if _other_kind(text, kind):
        return "other kind"
    if not ha and _HA_ONLY.search(text):
        return "home assistant"
    if _OUTSIDE_AI.search(text):
        return "outside ai"
    return ""


def _other_kinds(items: list[dict], row_of: dict[str, Optional[dict]], kind: str) -> int:
    """Finds made for another kind of the thing the bot is about (a paper
    printer tool for a 3D printer bot, :func:`_other_kind`) are left out:
    matched on the bare word "printer".
    Returns how many lines were left out."""
    gone = [i for i in items if i.get("section") in _ADDABLE_SECTIONS
            and row_of.get(i["id"]) and _other_kind(_own_text(row_of.get(i["id"])), kind)]
    if gone:
        items[:] = [i for i in items if all(i is not g for g in gone)]
    return len(gone)


#: Things a built page needs to have a page of its own when no index page was
#: seen at the top of the project (they make one while building).
_PAGE_MAKERS = frozenset({"next", "nuxt", "gatsby", "@sveltejs/kit", "astro", "react-scripts",
                          "@angular/core", "@angular/cli", "@vue/cli-service", "vite-plugin-html",
                          "html-webpack-plugin", "parcel", "parcel-bundler", "@11ty/eleventy",
                          "vitepress", "vuepress", "@docusaurus/core", "@remix-run/react",
                          "react-app-rewired", "@quasar/app", "@ionic/react", "@ionic/angular",
                          "@builder.io/qwik", "solid-start", "expo",
                          # A page framework or a page dev server: its page may sit
                          # in a folder the closer look does not read (src/index.html).
                          "vite", "@vitejs/plugin-react", "@vitejs/plugin-vue",
                          "@sveltejs/vite-plugin-svelte", "webpack-dev-server", "react-dom",
                          "vue", "svelte", "preact", "solid-js"})


#: Build parts that no longer build on today's Node in the locked box (a 2018
#: app dies in node-sass, "Node Sass does not yet support
#: your current environment").
_DEAD_BUILD_PARTS = frozenset({"node-sass"})


def _built_deps(row: dict) -> Optional[set]:
    details = row.get("details") if isinstance(row.get("details"), dict) else {}
    pj = details.get("package_json") if isinstance(details.get("package_json"), dict) else None
    return None if pj is None else {str(d) for d in pj.get("deps") or []}


def no_page(row: Optional[dict]) -> bool:
    """A web project that is built, with no page of its own to open: no index
    page in it and nothing in its parts that makes one (a Home Assistant card,
    a script for another program). It can never be an app in her bot (for
    example a dashboard card built to one .js file and no page)."""
    row = row or {}
    if str(row.get("mode_guess") or "") != "web_build":
        return False
    details = row.get("details") if isinstance(row.get("details"), dict) else {}
    if details.get("entry_html"):
        return False
    deps = _built_deps(row)
    if deps is None:
        return False      # nothing read: cannot tell
    return not (deps & _PAGE_MAKERS)


def dead_build(row: Optional[dict]) -> bool:
    """A web project whose build needs a part that no longer builds."""
    row = row or {}
    if str(row.get("mode_guess") or "") != "web_build":
        return False
    return bool((_built_deps(row) or set()) & _DEAD_BUILD_PARTS)


def _cannot_be_used(items: list[dict], row_of: dict[str, Optional[dict]]) -> int:
    """Finds she could tick but never use here are left out: one made for Home
    Assistant when it is not set up on this computer, a web project with no
    page to open (:func:`no_page`) and one whose build needs a part that no
    longer builds (:func:`dead_build`). Returns how many lines were left out."""
    ha = _ha_in_use()
    gone = []
    for item in items:
        row = row_of.get(item["id"])
        if item.get("section") != "apps_code" or not row:
            continue
        for_ha = not ha and bool(_HA_ONLY.search(_own_text(row)))
        if for_ha or no_page(row) or dead_build(row):
            gone.append(item)
            if isinstance(row.get("meaning"), dict):
                row["meaning"]["state"] = "dropped"
                row["meaning"]["why_dropped"] = (
                    "made for Home Assistant" if for_ha else "no page of its own to open"
                    if no_page(row) else "its build needs a part that no longer works")
    if gone:
        items[:] = [i for i in items if all(i is not g for g in gone)]
    return len(gone)


def _same_pictures(items: list[dict]) -> int:
    """Two picture lines with the same title from the same author are one
    picture to her ("Event Decorations" twice by one author)."""
    seen: set[tuple[str, str]] = set()
    gone = []
    for item in items:
        if item.get("section") != "pictures" or str(item.get("id") or "").startswith(
                "pictures:buildbot:"):
            continue
        by = re.search(r"\bby ([^.]+)\.", str(item.get("detail") or ""))
        key = (str(item.get("label") or "").lower(), (by.group(1) if by else "").lower())
        if key in seen:
            gone.append(item)
        seen.add(key)
    if gone:
        items[:] = [i for i in items if all(i is not g for g in gone)]
    return len(gone)


def _guard_ticks(items: list[dict], row_of: dict[str, Optional[dict]], kind: str,
                 jobs: Optional[list[dict]] = None) -> None:
    """Pre-ticks she can trust: none that :func:`tick_doubt` doubts, only under
    a job she asked for (when she asked for any: a pre-tick on a job the brain
    added while her own jobs' best matches stay unticked reads as BuildBot
    choosing for her), and at most one app per job (two
    dashboards for one job compete; the best fit stays ticked)."""
    ha = _ha_in_use()
    own = {str(j.get("id")) for j in jobs or [] if j.get("source") in ("asked", "owner")}
    for item in items:
        if item.get("section") not in _ADDABLE_SECTIONS or not item.get("default_on"):
            continue
        if own and str(item.get("need")) not in own and _addable(item):
            item["default_on"] = False
            item["doubt"] = "not a job she asked for"
            continue
        doubt = tick_doubt(row_of.get(item["id"]), kind, ha=ha)
        if doubt:
            item["default_on"] = False
            item["doubt"] = doubt
    by_need: dict[str, list[dict]] = {}
    for item in items:
        if item.get("section") == "apps_code" and item.get("default_on"):
            by_need.setdefault(str(item.get("need")), []).append(item)

    def fit(item: dict) -> float:
        return float(((row_of.get(item["id"]) or {}).get("meaning") or {}).get("fit") or 0.0)
    for rows in by_need.values():
        for extra in sorted(rows, key=lambda i: -fit(i))[1:]:
            extra["default_on"] = False


def _best_rank(row: Optional[dict]) -> Optional[tuple]:
    """How good a best match this find would be (higher is better), or None
    when it can never be one: the brain said it does not do the job or was
    made for something else, or it fits under :data:`meaning.MIGHT_FIT` (a
    find nothing describes never reaches the plan: ``meaning.score``). A
    brain's "yes" counts first, then "unsure", then no answer (a busy
    brain), then the fit."""
    m = (row or {}).get("meaning") or {}
    if m.get("state") not in ("might_help", "ticked"):
        return None
    if m.get("judge") == "no" and m.get("purpose") != "yes" or m.get("purpose") == "no":
        return None
    fit = float(m.get("fit") or 0.0)
    if fit < meaning.MIGHT_FIT:
        return None
    said = {"yes": 3, "unsure": 1}
    return (said.get(str(m.get("purpose")), 0) + said.get(str(m.get("judge")), 0), fit)


def _mark_best(items: list[dict], row_of: dict[str, Optional[dict]], jobs: list[dict],
               kind: str = "") -> None:
    """The top unticked fit of every job that has nothing ticked says so in
    plain words, so the plan is easy to act on; the marker never ticks it.

    A find is a fit when the brain did not say no to it (:func:`_best_rank`)
    and :func:`tick_doubt` has nothing against it; a single stock picture is
    never the best match for a job. With no fit the job has no best match:
    nothing is better than a weak one."""
    ticked = {str(i.get("need")) for i in items if i.get("default_on") and _addable(i)}
    ha = _ha_in_use()
    best: dict[str, tuple] = {}
    for item in items:
        need = str(item.get("need") or "")
        if not need or need in ticked or item.get("default_on") \
                or item.get("section") not in BEST_SECTIONS:
            continue
        if item.get("section") == "pictures":
            continue   # a single stock picture is never the best match for a job
        row = row_of.get(item["id"])
        rank = _best_rank(row)
        if rank is None or tick_doubt(row, kind, ha=ha):
            continue
        if need not in best or rank > best[need][0]:
            best[need] = (rank, item)
    for _rank, item in best.values():
        item["best_match"] = True
        item["detail"] = f"{BEST_MATCH} {item.get('detail') or ''}".strip()


#: Two finds of one job are the same kind of thing at this cosine or more
#: (measured on names and descriptions: the band goal's six metronomes, each
#: 0.67 or more with at least one other; two pomodoro timers 0.86; different
#: kinds 0.08 to 0.29: a metronome and a tuner, a pomodoro timer and a study
#: log, flashcards and a grammar checker, a budget and a loan calculator).
SAME_KIND = 0.60
#: At most this many finds of one kind are shown per job.
SAME_KIND_SHOWN = 2
#: The forms a group can open and see first (a web page, built or not); a
#: command-line tool last.
_FORM_ORDER = {"static_web": 0, "web_build": 0, "data_template": 1, "cli_tool": 2}


def _kind_text(row: dict) -> str:
    """What a find is, in its own name and words (no topic tags: they are
    about how it is made, and they pulled the band goal's metronomes apart)."""
    return f"{_clean(row.get('name'), 60)}. {_clean(row.get('what_it_is'), 200)}"


def _same_kind(items: list[dict], row_of: dict[str, Optional[dict]]) -> int:
    """Per job, finds that are the same kind of thing (nine metronomes) are
    shown at most :data:`SAME_KIND_SHOWN` times and ticked at most once; the
    one ticked is a form she can open and see (a web page before a command-line
    tool), then the best fit. Returns how many lines were left out."""
    code = [i for i in items if i.get("section") == "apps_code" and row_of.get(i["id"])]
    if len(code) < 2 or meaning.helper_state() != "ready":
        return 0
    try:
        vecs = meaning.embed([_kind_text(row_of[i["id"]]) for i in code])
    except Exception:  # noqa: BLE001 - no reading helper: nothing is merged
        return 0
    at = {i["id"]: k for k, i in enumerate(code)}

    def rank(item: dict) -> tuple:
        mode = str((item.get("facts") or {}).get("mode") or "")
        fit = float(((row_of.get(item["id"]) or {}).get("meaning") or {}).get("fit") or 0.0)
        return (not item.get("default_on"), _FORM_ORDER.get(mode, 4), -fit)

    drop: set[str] = set()
    by_need: dict[str, list[dict]] = {}
    for item in code:
        by_need.setdefault(str(item.get("need")), []).append(item)
    for rows in by_need.values():
        groups: list[list[dict]] = []
        for item in rows:
            for g in groups:
                if max(float(vecs[at[item["id"]]] @ vecs[at[x["id"]]]) for x in g) >= SAME_KIND:
                    g.append(item)
                    break
            else:
                groups.append([item])
        for g in groups:
            if len(g) < 2:
                continue
            # One tick per kind: a form she can see first, then the best fit.
            ticked = sorted((i for i in g if i.get("default_on")),
                            key=lambda i: rank(dict(i, default_on=True)))
            for extra in ticked[1:]:
                extra["default_on"] = False
            order = sorted(g, key=rank)
            for extra in order[SAME_KIND_SHOWN:]:
                drop.add(extra["id"])
    for item in items:
        if item["id"] in drop:
            row = row_of.get(item["id"]) or {}
            if isinstance(row.get("meaning"), dict):
                row["meaning"]["state"] = "dropped"
                row["meaning"]["why_dropped"] = "more than enough like it"
    items[:] = [i for i in items if i["id"] not in drop]
    return len(drop)


_ADDABLE_SECTIONS = ("apps_code", "pictures", "knowledge", "live_info")


def _addable(item: dict) -> bool:
    return item.get("section") in _ADDABLE_SECTIONS \
        and not str(item.get("id") or "").startswith("pictures:buildbot:")


def _about_the_bot(row: Optional[dict], kind: str) -> bool:
    """A strong find the brain said yes to, whose own words name what the bot
    is about ("knitting" for a knitting helper): it is about her bot even
    when it was filed under a job the brain added (so a knitting app at fit
    0.76 with the brain's yes is not left out of a knitting plan)."""
    m = (row or {}).get("meaning") or {}
    if (m.get("fit") or 0.0) < meaning.TICK_FIT or "yes" not in (m.get("judge"),
                                                                  m.get("purpose")):
        return False
    if "no" in (m.get("judge"), m.get("purpose")) and m.get("purpose") != "yes":
        return False
    from puffpai.catalogs import goal_words
    said = {goal_words.singular(w) for w in re.findall(r"[a-z0-9]{3,}", str(kind or "").lower())}
    said -= {"bot", "helper", "assistant", "app", "planner", "tracker", "coach", "tutor",
             "buddy", "guide", "manager"}
    own = {goal_words.singular(w) for w in re.findall(
        r"[a-z0-9]{3,}", re.sub(r"([a-z])([A-Z])", r"\1 \2", _own_text(row)).lower())}
    return bool(said & own)


def _only_brain_jobs(items: list[dict], row_of: dict[str, Optional[dict]],
                     jobs: list[dict], kind: str = "") -> int:
    """A job the brain added is never the only source of what she can add:
    with nothing addable under any job she asked for, the finds under the
    brain's jobs are left out too (so a game-server plan cannot "pass" only
    through password libraries under "Secure login", a job the owner never
    asked for), unless the find is about the bot itself
    (:func:`_about_the_bot`). Returns how many lines were left out."""
    own = {str(j.get("id")) for j in jobs if j.get("source") in ("asked", "owner")}
    if not own or any(_addable(i) and str(i.get("need")) in own for i in items):
        return 0
    drop = [i for i in items if _addable(i) and str(i.get("need")) not in own
            and not _about_the_bot(row_of.get(i["id"]), kind)]
    for item in drop:
        row = row_of.get(item["id"]) or {}
        if isinstance(row.get("meaning"), dict):
            row["meaning"]["state"] = "dropped"
            row["meaning"]["why_dropped"] = "only under a job the brain added"
    ids = {i["id"] for i in drop}
    items[:] = [i for i in items if i["id"] not in ids]
    return len(drop)


# -- the fallback with no jobs -------------------------------------------------------
class _Ctx:
    """Just enough of a summary context for the older job-less providers."""

    def __init__(self, goal: str, online: bool, cfg: Any) -> None:
        self.goal, self.cfg, self.internet = goal, cfg, bool(online)
        self.previews: dict = {}
        self.shared: dict = {}
        self.terms: list = []
        self.args = None
        self.name = ""


def _fallback(goal: str, online: bool, cfg: Any) -> list[dict]:
    """With no jobs, the books the older providers offer."""
    from puffpai.wizard import factory
    ctx = _Ctx(goal, online, cfg)
    out: list[dict] = []
    for provider in (factory.knowledge_items,):
        try:
            out.extend(dict(i) for i in provider(ctx) or [] if isinstance(i, dict))
        except Exception:  # noqa: BLE001 - a provider never sinks the plan
            continue
    return out


# -- searching ------------------------------------------------------------------------
def _trust(found: dict, tf) -> None:
    if isinstance(found.get("trust"), dict) and "floor_ok" in found["trust"]:
        return
    if tf is None:
        found["trust"] = {"floor_ok": False, "path": "", "pretick_ok": False,
                          "reasons": ["not in this build yet"]}
        return
    try:
        ok, path, reasons = tf.check(found)
        pre = bool(tf.pretick_allowed(found))
    except Exception:  # noqa: BLE001 - a check that fails trusts nothing
        ok, path, reasons, pre = False, "", ["check failed"], False
    found["trust"] = {"floor_ok": bool(ok), "path": str(path or ""), "pretick_ok": pre,
                      "reasons": [str(r) for r in reasons or []]}


def _live_trust(found: dict) -> None:
    """A live-info row may start ticked only when it is on BuildBot's own
    checked list, its last check passed, and it needs no key (as before)."""
    if found.get("part") != "live-info" or found.get("source") != "seeds":
        return
    try:
        from puffpai.wizard import factory
        cand = factory.candidate(str(found.get("id"))) or {}
    except Exception:  # noqa: BLE001
        cand = {}
    trust = dict(found.get("trust") or {"floor_ok": True, "path": "", "reasons": []})
    trust["pretick_ok"] = bool(cand.get("verified_at") and cand.get("auth") == "none")
    found["trust"] = trust


def _live_labels(wtl, kind: str) -> list[str]:
    try:
        return [str(r.get("label") or r.get("id")) for r in wtl.route(kind)
                if r.get("role") == "live" and r.get("enabled", True)]
    except Exception:  # noqa: BLE001
        return []


def _all_later(wtl, budget, kinds: list[str]) -> bool:
    ids = []
    try:
        for kind in kinds:
            ids += [str(r.get("id")) for r in wtl.route(kind)
                    if r.get("role") == "live" and r.get("enabled", True)]
        return bool(ids) and all(budget.later(i) for i in ids)
    except Exception:  # noqa: BLE001
        return False


def _kinds_of(job: dict) -> list[str]:
    return [str(p.get("kind")) for p in job.get("parts") or []]


#: Words that say how a project is programmed, not what it does for a person.
_DEV_WORDS = re.compile(
    r"\b(?:react|vue|svelte|angular|next\.?js|node|npm|yarn|typescript|javascript|css|html|"
    r"api|apis|sdk|frontend|front-end|backend|back-end|props|component|components|docker|"
    r"database|postgres|sqlite|mongodb|firebase|supabase|llm|ollama|openai|gpt|deploy|"
    r"server|build|lint|tests?|ci|github|repo|framework|library|open[- ]source|self-?hosted|"
    r"licen[cs]e|mit|docs?|documentation|install|setup|demo)\b", re.I)
#: A plain job line is 3 to 7 words (two words are mostly a heading: "Sync Providers").
SIMILAR_WORDS = (3, 7)


_VERBS: set[str] = set()


def _is_verb(word: str) -> bool:
    """Is *word* a doing word ("Manage", "Print")? BuildBot's own
    list of job verbs, or a word the shipped word list gives an -ing and an
    -ed form and no possessive ("guest" has all three: a thing, not a doing)."""
    if not _VERBS:
        from puffpai.catalogs import needs_rules
        _VERBS.update(needs_rules.VERBS)
        try:
            with open(needs_rules._dict_path(), encoding="utf-8", errors="replace") as fh:
                next(fh, None)
                for line in fh:
                    head, _, flags = line.strip().partition("/")
                    if head.isalpha() and head.islower() and "G" in flags and "D" in flags \
                            and "M" not in flags:
                        _VERBS.add(head)
        except OSError:
            pass
    # The word as it is: a line that starts "Manage ..." says a thing to do;
    # "Notes from a meeting" starts with a thing.
    return str(word or "").lower() in _VERBS


def _similar_name(lines: list[str]) -> str:
    """The plain job a cluster names: the most central README line that is a
    short thing to do as its author wrote it ("Manage your guest list"),
    never a list of keywords picked out of it (not "guest list manage
    send"). A heading glued to its sentence ("Vendor Management:
    Organize and communicate with vendors") gives the sentence. "" when no
    line says a thing to do plainly."""
    lo, hi = SIMILAR_WORDS
    for line in lines:
        for piece in reversed(re.split(r"\s*(?::|\s-\s|\s\u2013\s|\s\u2014\s)\s*", str(line))):
            text = " ".join(re.sub(r"[^\w\s,'&-]", " ", piece).split()).strip(" ,-")
            words = text.split()
            while words and (len(words[0]) == 1 or words[0].lower().endswith("ly")):
                words.pop(0)   # "T Tap tempo", "Automatically detects new tracks"
            text = " ".join(words)
            if not lo <= len(words) <= hi or not text[:1].isalpha() or not _is_verb(words[0]):
                continue
            if text.isupper() or re.search(r"\d{3,}|https?|www\.", text):
                continue
            return text[:1].upper() + text[1:]
    return ""


def _similar(scored: list[dict], jobs: list[dict], kind: str) -> list[dict]:
    """Things several fitting projects also do that no ticked job covers.

    Feature lines of projects that fit (blurb >= 0.40) whose best job is under
    0.50 but that still relate to the subject (>= 0.20) are grouped at 0.50;
    a group seen in 2 or more projects becomes one "Similar projects also
    have" entry named by its most central short README line, or left off
    when no line says it plainly. Needs the reading helper.
    """
    if meaning.helper_state() != "ready" or not jobs:
        return []
    try:
        import numpy as np
    except Exception:  # noqa: BLE001
        return []
    lines: list[tuple[str, str]] = []           # (line, find id)
    for f in scored:
        m = f.get("meaning") or {}
        if (m.get("blurb") or 0.0) < 0.40 or meaning.is_catalog_row(f):
            continue
        for line in meaning._lines_of(f):
            lines.append((line, str(f.get("id"))))
    if not lines:
        return []
    lv = meaning.embed([ln for ln, _ in lines])
    jv = meaning.embed([meaning.job_text(kind, j) for j in jobs])
    subj = meaning.embed([kind or " "])[0]
    best_job = (lv @ jv.T).max(axis=1)
    rel = lv @ subj
    keep = [k for k in range(len(lines)) if best_job[k] < 0.50 and rel[k] >= 0.20]
    clusters: list[list[int]] = []
    for k in keep:
        for c in clusters:
            if float(lv[k] @ lv[c[0]]) >= SIMILAR_CLUSTER:
                c.append(k)
                break
        else:
            clusters.append([k])
    out = []
    for c in clusters:
        projects = sorted({lines[k][1] for k in c})
        if len(projects) < SIMILAR_MIN_PROJECTS:
            continue
        if any(_DEV_WORDS.search(lines[k][0]) for k in c):
            continue  # about how it is programmed, not about something it does for her
        name = _similar_name([lines[k][0] for k in
                              sorted(c, key=lambda k: -float(sum(lv[k] @ lv[m] for m in c)))])
        if not name:
            continue  # no plain way to say it: left off rather than word salad
        out.append({"name": name, "seen_in": len(projects),
                    "items": [_item_id({"id": p}) for p in projects]})
    out.sort(key=lambda r: -r["seen_in"])
    return out[:SIMILAR_MAX]


#: Searches for different jobs asked at once (each site keeps its own pace).
SEARCH_WORKERS = 3

#: GitHub's own order is kept: at most this many finds of one search are
#: scored (measured: small fitting projects sat at places 17 to 60).
KEEP_PER_CALL = 80


class _Warmer:
    """Reads finds with the reading helper in one background thread (cache only)."""

    def __init__(self) -> None:
        import concurrent.futures
        self._pool = concurrent.futures.ThreadPoolExecutor(1, thread_name_prefix="meaning-warm")
        # For the 90 s margin: how many texts wait,
        # and how fast this computer reads them now (texts a second).
        self._lock = threading.Lock()
        self._pending = 0
        self._done = 0
        self._busy_s = 0.0

    def _run(self, texts: list[str]) -> None:
        start = time.monotonic()
        try:
            _warm(texts)
        finally:
            with self._lock:
                self._pending -= len(texts)
                self._done += len(texts)
                self._busy_s += time.monotonic() - start

    def _submit(self, texts: list[str]) -> None:
        with self._lock:
            self._pending += len(texts)
        try:
            self._pool.submit(self._run, texts)
        except RuntimeError:     # finished already: a late look is not scored
            with self._lock:
                self._pending -= len(texts)

    def add(self, rows: list[dict]) -> None:
        self._submit([meaning.candidate_text(r) for r in rows])

    def add_texts(self, texts: list[str]) -> None:
        if not texts:
            return
        self._submit(list(texts))

    def backlog_s(self) -> float:
        """About how long the texts still waiting take at the speed measured
        so far (0 until something was read: unknown is not held back)."""
        with self._lock:
            if self._done < WARM_RATE_MIN_TEXTS or self._busy_s <= 0:
                return 0.0
            return self._pending / (self._done / self._busy_s)

    def finish(self, wait: bool = True) -> None:
        self._pool.shutdown(wait=wait, cancel_futures=not wait)


#: How long the closer looks still being read are waited for after their end time.
LOOK_GRACE_S = 3.0
#: For the 90 s margin: no new closer look starts while
#: the README lines already read would take longer than this to read with the
#: reading helper at the speed measured in this build. Lines not read by the
#: end of the looks are read again by the second scoring, which took 10 to 21 s
#: on a busy computer (15.5 s and 21 s on two goals), and
#: that pushed plans past 90 s. On a quiet computer nothing waits.
WARM_BACKLOG_S = 6.0
#: The speed is known only after this many texts were read.
WARM_RATE_MIN_TEXTS = 40
#: How much a job that already had one closer look gives way to the others.
LOOK_JOB_PENALTY = 0.04
#: Closer looks at once per source. Each website is asked about once a
#: second, so looks queued on one website only wait for each other; measured
#: (garden goal): all six looks waited on Codeberg for half a
#: minute while GitHub's two file hosts sat idle. GitHub projects are read from
#: two hosts (one archive, or file by file), so they get more.
LOOKS_PER_SOURCE = {"github": 4}
LOOKS_PER_OTHER_SOURCE = 2


#: A look at a project too big to read as one archive asks a dozen small files
#: one a second; such projects are mostly servers (measured: 8 of 10 looks on one goal went to big server apps and the
#: small fitting tools were "not looked at in time").
BIG_LOOK_PENALTY = 0.05
#: A project the self-hosting list says runs only on a server platform (PHP,
#: Python, Go, Java ...) can never open inside her bot.
SERVER_ONLY_PENALTY = 0.10
#: Platforms on that list that can still be a web page or a web app built in the box.
_WEB_PLATFORMS = frozenset({"javascript", "nodejs", "deno", "unknown"})


def _listed_server_only(row: dict) -> bool:
    """Does the shipped self-hosting list say *row* runs only on server platforms?"""
    try:
        from puffpai.catalogs.searchers import awesome_selfhosted
        listed = awesome_selfhosted.listed(str(row.get("url") or ""))
    except Exception:  # noqa: BLE001 - no list: cannot tell
        return False
    plats = {str(p).lower() for p in (listed or {}).get("platforms") or []}
    plats.discard("docker")
    plats.discard("k8s")
    plats.discard("deb")
    return bool(plats) and not (plats & _WEB_PLATFORMS)


def _look_worth(row: dict, blurb: float) -> float:
    """How likely a closer look at *row* ends in something she can add: the
    fit, more for code written for the web, more still when people use it
    (5 stars or more, which may also be pre-ticked); less for a project too
    big to read as one archive, and less again for one the self-hosting list
    says runs only on a server."""
    repo = row.get("repo") if isinstance(row.get("repo"), dict) else {}
    web = str(repo.get("language") or "").lower() in meaning.WEB_LANGUAGES
    used = meaning._stars(row) >= 5
    worth = blurb + (0.04 if web else -0.06) + (0.04 if used and web else 0.0)
    if repo.get("host") == "github.com":
        gh = _optional("puffpai.catalogs.searchers.github")
        small = getattr(gh, "small_enough", None) if gh is not None else None
        if callable(small) and not small(repo):
            worth -= BIG_LOOK_PENALTY
    if row.get("source") == "awesome_selfhosted" and _listed_server_only(row):
        worth -= SERVER_ONLY_PENALTY
    return worth


class _CloserLooks:
    """Closer looks that start while the searches still run.

    A closer look reads a project's own files (other websites than the
    search), one small file a second per website, so looks only after the
    last search left too little time for more than a handful. Here a scorer
    thread reads each batch of finds as it arrives (the reading helper, stage
    1 only), files every licensed code find under the job it fits best, and
    two look threads take the likeliest addable find of each job in turn
    (:func:`meaning.closer_look_tier`, then fit) until *until*. A find with no
    open licence is never looked at: at best it is a note.
    """

    def __init__(self, wtl, budget, jobs: list[dict], kind: str, until: float,
                 workers: int = 2) -> None:
        self.wtl, self.budget, self.until = wtl, budget, until
        self.jobs = [j for j in jobs if j.get("ticked", True)]
        self.kind = kind
        self.read: dict[str, dict] = {}
        self._cv = threading.Condition()
        self._pending: list[dict] = []
        self._cands: dict[str, list[tuple]] = {str(j["id"]): [] for j in self.jobs}
        self._rows: dict[str, dict] = {}
        self._started: set[str] = set()
        self._adding = True
        self._closed = False
        self._scored = False
        self._looks: dict[str, int] = {}
        self._busy: dict[str, int] = {}
        self._warm = _Warmer()
        self._threads = [threading.Thread(target=self._score_loop, name="closer-score",
                                          daemon=True)]
        self._threads += [threading.Thread(target=self._look_loop, name=f"closer-look-{n}",
                                           daemon=True) for n in range(max(1, workers))]
        for t in self._threads:
            t.start()

    def add(self, rows: list[dict]) -> None:
        with self._cv:
            self._pending.extend(rows)
            self._cv.notify_all()

    def finish(self) -> dict[str, dict]:
        with self._cv:
            self._adding = False
            self._cv.notify_all()
        # A look still reading at its end time sends nothing more; it is waited
        # for only a moment, and one that is later than that is not used
        # (a slow archive must not hold the plan past 90 s).
        end = max(self.until, time.monotonic()) + LOOK_GRACE_S
        for t in self._threads:
            t.join(timeout=max(0.0, end - time.monotonic()))
        with self._cv:
            self._closed = True
            read = dict(self.read)
        self._warm.finish(wait=False)
        return read

    def _score_loop(self) -> None:
        try:
            jv = meaning.embed([meaning.job_text(self.kind, j) for j in self.jobs]) \
                if self.jobs else None
            while True:
                with self._cv:
                    while not self._pending and self._adding:
                        self._cv.wait(timeout=0.5)
                    batch, self._pending = self._pending, []
                    if not batch and not self._adding:
                        return
                rows = [r for r in batch if not meaning.is_catalog_row(r)
                        and meaning.closer_look_tier(r) <= 1
                        and not meaning.page_words(r) and not meaning._orders(r)
                        and not meaning.trust_hopeless(r)]
                if not rows or jv is None:
                    continue
                vecs = meaning.embed([meaning.candidate_text(r) for r in rows])
                sims = vecs @ jv.T
                with self._cv:
                    for r, row_sims in zip(rows, sims):
                        best = int(row_sims.argmax())
                        blurb = float(row_sims[best])
                        if blurb < meaning.LOOK_MIN_BLURB:
                            continue
                        rid = str(r["id"])
                        self._rows[rid] = r
                        self._cands[str(self.jobs[best]["id"])].append(
                            (_look_worth(r, blurb), rid))
                    self._cv.notify_all()
        except Exception:  # noqa: BLE001 - no early looks; the later path still scores
            pass
        finally:
            with self._cv:
                self._scored = True
                self._cv.notify_all()

    def _next(self) -> Optional[dict]:
        """The likeliest addable find overall; a job that already had looks
        gives way a little, so one job does not take every look."""
        best: Optional[tuple] = None
        for jid, lst in self._cands.items():
            penalty = LOOK_JOB_PENALTY * self._looks.get(jid, 0)
            for worth, rid in lst:
                if rid in self._started or not self._free(self._rows[rid]):
                    continue
                if best is None or worth - penalty > best[0]:
                    best = (worth - penalty, jid, rid)
        if best is None:
            return None
        self._started.add(best[2])
        self._looks[best[1]] = self._looks.get(best[1], 0) + 1
        row = self._rows[best[2]]
        src = str(row.get("source") or "")
        self._busy[src] = self._busy.get(src, 0) + 1
        return row

    def _free(self, row: dict) -> bool:
        """May one more look start on this find's source now?"""
        src = str(row.get("source") or "")
        return self._busy.get(src, 0) < LOOKS_PER_SOURCE.get(src, LOOKS_PER_OTHER_SOURCE)

    def _done(self, row: dict) -> None:
        with self._cv:
            src = str(row.get("source") or "")
            self._busy[src] = max(0, self._busy.get(src, 0) - 1)
            self._cv.notify_all()

    def looked_at(self) -> set[str]:
        """Every find a look was started for (read or not)."""
        with self._cv:
            return set(self._started)

    def _look_loop(self) -> None:
        while True:
            with self._cv:
                while True:
                    if time.monotonic() > self.until:
                        return
                    # The reading helper is behind: wait for it, so the second
                    # scoring has little left to read.
                    held = self._warm.backlog_s() > WARM_BACKLOG_S
                    row = None if held else self._next()
                    if row is not None:
                        break
                    if not held and self._scored and not self._pending \
                            and not any(self._busy.values()):
                        return
                    self._cv.wait(timeout=0.5)
            try:
                got = self._read(row)
            except Exception:  # noqa: BLE001 - one find that cannot be read stays as it was
                got = None
            finally:
                self._done(row)
            if isinstance(got, dict):
                with self._cv:
                    if self._closed:
                        return           # too late to be used
                    self.read[str(row["id"])] = got
                # Its README lines are read by the reading helper now (the
                # answers are cached), so scoring after the looks is quick.
                self._warm.add_texts(meaning._lines_of(got))

    def _read(self, row: dict) -> Optional[dict]:
        """One closer look that asks nothing after *until* (fewer facts, never more trust)."""
        with _no_request_after(self.until):
            return self.wtl.details(row, budget=self.budget)


def _no_request_after(at: float):
    """No new request after *at* on this thread (a search cut short finds less)."""
    searchers = _optional("puffpai.catalogs.searchers")
    stop = getattr(searchers, "deadline", None) if searchers is not None else None
    return stop(at) if stop is not None else contextlib.nullcontext()


class OutOfTime(Exception):
    """A wait for a source would end after the search time."""


def _bounded_sleep(sleep: Callable[[float], None], until: float) -> Callable[[float], None]:
    def bounded(seconds: float) -> None:
        if time.monotonic() + float(seconds) > until:
            raise OutOfTime()
        sleep(seconds)
    return bounded


def _warm(texts: list[str]) -> None:
    try:
        meaning.embed(texts)
    except Exception:  # noqa: BLE001 - scoring reads them again if this fails
        pass


#: The judge answers ten short questions at once.
JUDGE_TOKENS = 200
#: The judge asks nothing in the last seconds of the search time (the plan is
#: put together after it).
JUDGE_RESERVE_S = 9.0
#: One question to the judge never takes longer than this (the brain is shared:
#: measured, one answer took 9 s while other work used it).
JUDGE_TIMEOUT_S = 5
#: Would-be ticks may still be asked about until this long before the search
#: time ends: one question's longest wait and a second to put the plan
#: together (measured: about 0.2 s), so the search stays inside its time.
JUDGE_TICKS_RESERVE_S = JUDGE_TIMEOUT_S + 1.0


def _judge_client(cfg: Any):
    """The brain as a judge, only when it is known to answer 20 tokens a
    second or more (a slower brain would hold the plan up); else None."""
    try:
        from puffpai.wizard import needs as needs_mod, needs_llm, relevance
        url, model = needs_mod._brain(cfg)
        speed = needs_mod._known_speed(cfg, model) if model else None
        if not url or not model or speed is None or speed < needs_llm.FAST_TOK_S \
                or needs_llm.choose_prompt(model, speed) == "rules":
            return None
        # The same context size as the needs step: a different one makes the
        # brain load again before the first question (measured: every question
        # then ran out of time and nothing was checked).
        return relevance.memo_client(url, model, max_tokens=JUDGE_TOKENS,
                                     timeout=JUDGE_TIMEOUT_S, num_ctx=needs_llm.NUM_CTX)
    except Exception:  # noqa: BLE001 - no judge is fine
        return None


def _warm_judge(cfg: Any) -> None:
    """Load the brain for the judge in the background (a fixed one-word
    question, the judge's own context size): the brain is shared, and loading
    it again takes longer than one question may. Never raises."""
    def run() -> None:
        try:
            from puffpai.wizard import needs as needs_mod, needs_llm, relevance
            url, model = needs_mod._brain(cfg)
            ask = relevance.client(url, model, max_tokens=1, timeout=30,
                                   num_ctx=needs_llm.NUM_CTX)
            if ask is not None:
                ask([{"role": "user", "content": "ok"}])
        except Exception:  # noqa: BLE001 - a warm-up that fails changes nothing
            pass
    threading.Thread(target=run, name="judge-warm", daemon=True).start()


class _JudgeAhead:
    """The judge asked about the first look's lines while the closer looks and
    the second look run (the brain is idle then). Its answers are kept by the
    memo client, so the judge after the second look gets the same questions
    answered at once (otherwise, when the second look ends half a second
    before the judge's stop time, one question is answered, and whether a goal
    has a pre-ticked line depends on that timing)."""

    def __init__(self, scored: list[dict], jobs: list[dict], ask, *, stop_at: float,
                 kind: str) -> None:
        self._halt = threading.Event()

        def guarded(messages, **kw):
            if self._halt.is_set():
                raise RuntimeError("stopped")
            return ask(messages, **kw)

        rows = copy.deepcopy(scored)
        # A line may become a tick after the second look: its tick question
        # (about her job it would be ticked under) is asked ahead too.
        for f in scored:
            m = f.get("meaning") or {}
            if m.get("state") == "might_help" and m.get("own_job")                     and not meaning.is_catalog_row(f)                     and (m.get("blurb") or 0.0) >= meaning.TICK_FIT:
                # Under her job, or under the job it fits best when it fits
                # hers only moderately (meaning.score): both are asked ahead.
                for jid in dict.fromkeys([m["own_job"], m.get("best_job") or m["own_job"]]):
                    extra = copy.deepcopy(f)
                    extra["meaning"].update(state="ticked", job=jid)
                    rows.append(extra)
        self._thread = threading.Thread(
            target=lambda: meaning.judge(rows, jobs, guarded, stop_at=stop_at, kind=kind),
            name="judge-ahead", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """No new question; the one being answered is waited for (at most one
        question's time), so it is not asked twice."""
        self._halt.set()
        self._thread.join(timeout=JUDGE_TIMEOUT_S)


def _judge_ahead(scored: list[dict], jobs: list[dict], cfg: Any, *, stop_at: float,
                 kind: str) -> Optional[_JudgeAhead]:
    try:
        ask = _judge_client(cfg)
        return None if ask is None else _JudgeAhead(scored, jobs, ask, stop_at=stop_at,
                                                    kind=kind)
    except Exception:  # noqa: BLE001 - no judge ahead is fine
        return None


#: The outside services a find uses ("What leaves your computer" rows).
_FINDERS = ("code_search", "code_download", "picture_search", "picture_download",
            "app_parts", "library", "catalog_refresh", "wikipedia_books",
            "gutenberg_books", "openstax_books", "wikibooks_books", "archive_books")


def _finders_mark() -> tuple:
    """Which finding services are off right now. Unreadable: never reused."""
    try:
        from puffpai.security import outside_switches
        return tuple(bool(outside_switches.is_off(s)) for s in _FINDERS)
    except Exception:  # noqa: BLE001 - not known: a key no later call matches
        return ("unknown", time.monotonic())


def plan_items(needs: dict, *, goal: str, online: bool, allow_send: bool, cfg,
               on_line: Optional[Callable[[str], None]] = None) -> dict:
    """Plan lines grouped by job, notes, jobs not reached, sends, left-out count."""
    # Which finding services may be
    # used is part of the answer, so a search refused while one was off is
    # searched again once she switches it on, never replayed from memory.
    key = (_digest(needs), bool(online), bool(allow_send), _finders_mark())
    with _LOCK:
        memo = _MEMO.get(key)
        no_jobs = bool((_EVIDENCE.get(key) or {}).get("fallback"))
    if memo is not None:
        if no_jobs and on_line is not None:
            # Drawn again with no jobs: say again where the lines came from,
            # so nothing is left to be called a fit for "your jobs".
            on_line(LINE_NO_JOBS.format(n=len(memo.get("items") or ())))
        return copy.deepcopy(memo)
    said: set[str] = set()
    # Searches run on several threads: finds, lines and counts change under this.
    _SEARCH_LOCK = threading.RLock()

    def say(line: str) -> None:
        # One line once per build: "not in this build yet" is never repeated.
        with _SEARCH_LOCK:
            if on_line is None or line in said:
                return
            said.add(line)
            on_line(line)

    all_jobs = [j for j in needs.get("jobs") or []]
    jobs = _jobs_in_order(needs)
    result: dict = {"items": [], "notes": {}, "not_reached": [], "similar": [], "sends": [],
                    "left_out": 0}
    evidence: dict = {"jobs": [{"id": j["id"], "name": j.get("name"), "parts": _kinds_of(j)}
                               for j in jobs], "finds": [], "fallback": False}

    def finish() -> dict:
        with _LOCK:
            _MEMO[key] = copy.deepcopy(result)
            _EVIDENCE[key] = evidence
            _LAST.append(key)
            del _LAST[:-4]
        return copy.deepcopy(result)

    if not all_jobs:
        result["items"] = _fallback(goal, online, cfg)
        evidence["fallback"] = True
        say(LINE_NO_JOBS.format(n=len(result["items"])))
        return finish()

    code_jobs = [j for j in jobs if any(k in CODE_PARTS for k in _kinds_of(j))]
    wtl = _optional("puffpai.catalogs.where_to_look")
    if wtl is None:
        for job in code_jobs:
            result["notes"].setdefault(job["id"], []).append(NOT_IN_BUILD)
        return finish()
    tf = _optional("puffpai.catalogs.trust_floor")
    oq = _optional("puffpai.security.outbound_query")

    sendables: list = []
    if online and allow_send and oq is not None:
        from puffpai.wizard import needs as needs_mod
        sendables = [s for s in needs_mod.sends(needs, goal=goal) if oq.is_sendable(s)]
    by_job: dict[str, list] = {}
    subject = None
    for s in sendables:
        if getattr(s, "subject", False) or s.source_hint == "subject":
            subject = subject or s
        else:
            by_job.setdefault(str(s.source_hint), []).append(s)

    budget = wtl.new_budget(wizard=True)
    t0 = time.monotonic()
    deadline = t0 + SEARCH_DEADLINE_S
    aim = t0 + min(PLAN_AIM_S, SEARCH_DEADLINE_S)    # the brain's checks end by the aim
    search_until = deadline - CLOSER_LOOK_S
    look_until = _look_until(deadline, jobs)
    if callable(getattr(budget, "sleeper", None)):
        # A source that asks us to wait (GitHub's 10 searches a minute are shared
        # by the whole home network) is waited for only inside the search time;
        # a longer wait gives that search up.
        budget.sleeper = _bounded_sleep(budget.sleeper, search_until)
    marks: dict[str, float] = {}
    evidence["timings"] = marks

    def mark(name: str) -> None:
        marks[name] = round(time.monotonic() - t0, 2)
    found: dict[str, dict] = {}
    hits: dict[str, list[str]] = {}                  # find id -> [where it was found]
    searched: list[str] = []
    stopped = False

    # While the next search waits on the network, the finds already here are
    # read by the reading helper in the background (its answers are cached),
    # so scoring after the last search does not start from nothing.
    helper = meaning.helper_state() == "ready"
    kind_phrase = str(needs.get("kind") or "")
    early = (_CloserLooks(wtl, budget, jobs, kind_phrase, until=look_until,
                          workers=CLOSER_WORKERS)
             if helper and online and allow_send and oq is not None else None)
    warm = _Warmer() if helper and early is None else None

    first_seen: dict[str, tuple[int, int]] = {}   # find id -> (search turn, place)
    closed = [False]                              # searching is over: late answers are not used

    def keep(rows: list[dict], how: str, rank: int = -1) -> int:
        n = 0
        new: list[dict] = []
        with _SEARCH_LOCK:
            if closed[0]:
                return 0
            for place, row in enumerate((rows or [])[:KEEP_PER_CALL]):
                if isinstance(row, dict) and row.get("id"):
                    fid = str(row["id"])
                    hits.setdefault(fid, []).append(how)
                    seen = (rank, place)
                    if fid not in first_seen or seen < first_seen[fid]:
                        first_seen[fid] = seen
                    if fid not in found:
                        found[fid] = copy.deepcopy(row)
                        new.append(row)
                        n += 1
        if early is not None and new:
            early.add(new)
        elif warm is not None and new:
            warm.add(new)
        return n

    def local_kinds(job: dict) -> list[str]:
        kinds = [k for k in _kinds_of(job) if k in CODE_PARTS or k == "live-info"]
        if job.get("learn") and "knowledge" in _kinds_of(job):
            kinds.append("knowledge")
        return kinds

    local_n = 0
    local_turn = -100000          # this computer's finds come before any search's
    for job in jobs:
        for kind in local_kinds(job):
            local_turn += 1
            try:
                local_n += keep(wtl.find_local(job, kind=kind), f"{job['id']}:{kind}:local",
                                rank=local_turn)
            except Exception:  # noqa: BLE001 - a broken snapshot finds nothing
                pass
        if "knowledge" in local_kinds(job):
            local_turn += 1
            local_n += keep(_book_rows(job, str(needs.get("kind") or "")),
                            f"{job['id']}:knowledge:books", rank=local_turn)
    say(LINE_LOCAL.format(n=local_n))

    def online_kinds(job: dict) -> list[str]:
        kinds = [k for k in _kinds_of(job) if k in CODE_PARTS]
        if "pictures" in _kinds_of(job) and meaning.wants_pictures(job):
            # Pictures only for a job a picture can do (never a stock photo
            # for "what a warning light means").
            kinds.append("pictures")
        if job.get("learn") and "knowledge" in _kinds_of(job):
            kinds.append("knowledge")
        return kinds

    # Searching online: the subject once, then every job's code search, then
    # data lists, then pictures and books, so the search allowance (10 GitHub
    # searches a build) reaches every job before any job gets a second kind.
    # Searching stops SEARCH_DEADLINE_S - CLOSER_LOOK_S after the start, so
    # the closer looks and the scoring still fit in the whole time.
    #
    # The searches run SEARCH_WORKERS at a time, in that order. One slow site
    # does not hold up every later job (measured: each Codeberg search took
    # 30 s and the jobs after it were never searched, on three of the twelve
    # bar goals). Each site keeps its
    # own pace and allowance (the budget counts under its lock).
    reached: set[str] = set()
    online_jobs = [j for j in jobs if online_kinds(j)]
    if not (online and allow_send and oq is not None):
        searched.extend(j["id"] for j in online_jobs)
    else:
        tasks: list[tuple[dict, list[str], list]] = []
        if subject is not None and online_jobs:
            tasks.append(({"id": "subject", "name": subject.phrase}, ["subject"], [subject]))
        passes = [["code-app"], ["data-template"], ["pictures", "knowledge"]]
        # Her own jobs are searched first, every kind, and only then the jobs
        # the brain added: GitHub's searches (10 a minute, shared by every
        # job) go to what she asked for.
        mine = [j for j in online_jobs if j.get("source") in ("asked", "owner")]
        others = [j for j in online_jobs if j not in mine]
        for group in (mine, others):
            for pass_kinds in passes:
                for job in group:
                    kinds = [k for k in online_kinds(job) if k in pass_kinds]
                    if kinds:
                        phrases = [s for s in by_job.get(job["id"], []) if oq.is_sendable(s)]
                        tasks.append((job, kinds, phrases))
        started: list[int] = []
        skipped_for_time: list[int] = []
        per_source: dict[str, int] = {}
        counted: set[str] = set()
        told: dict[str, float] = {}

        def tell(rows_got: list) -> None:
            """Each place's own line as it answers ("GitHub: 38 found"),
            again at most every :data:`SOURCE_LINE_EVERY_S` while it finds more."""
            now = time.monotonic()
            with _SEARCH_LOCK:
                grew = []
                for row in rows_got or []:
                    if isinstance(row, dict) and row.get("id") and str(row["id"]) not in counted:
                        counted.add(str(row["id"]))
                        src = str(row.get("source") or "")
                        per_source[src] = per_source.get(src, 0) + 1
                        if src not in grew:
                            grew.append(src)
                for src in grew:
                    if now - told.get(src, -1e9) >= SOURCE_LINE_EVERY_S:
                        told[src] = now
                        say(LINE_SOURCE.format(place=place_name(src), n=per_source[src]))
                left = getattr(budget, "remaining", None)
                if "github" in per_source and "github" not in told_spent \
                        and callable(left) and left("github_search") == 0:
                    told_spent.add("github")
                    say(LINE_GITHUB_SPENT)
        told_spent: set[str] = set()

        def run_task(n: int) -> None:
            job, kinds, phrases = tasks[n]
            if time.monotonic() > search_until:
                with _SEARCH_LOCK:
                    skipped_for_time.append(n)
                return
            if kinds != ["subject"] and _all_later(wtl, budget, kinds):
                return
            with _SEARCH_LOCK:
                started.append(n)
            with _no_request_after(search_until):
                for kind in kinds:
                    if not phrases or time.monotonic() > search_until:
                        break
                    try:
                        rows_got = wtl.find_online(job, phrases, kind=kind, budget=budget,
                                                   progress=say)
                    except Exception:  # noqa: BLE001 - a searcher that fails finds nothing
                        rows_got = []
                    tag = "subject:online" if kind == "subject" else f"{job['id']}:{kind}:online"
                    keep(rows_got, tag, rank=n)
                    tell(rows_got)

        import concurrent.futures
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=SEARCH_WORKERS,
                                                     thread_name_prefix="find-search")
        try:
            futures = [pool.submit(run_task, n) for n in range(len(tasks))]
            # A search still waiting on a slow site at the end is not waited
            # for: it sends nothing more, and what it finds late is not used.
            concurrent.futures.wait(
                futures, timeout=max(0.0, search_until - time.monotonic()) + LOOK_GRACE_S)
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
            with _SEARCH_LOCK:
                closed[0] = True
        with _SEARCH_LOCK:
            began = sorted(started)
            late = bool(skipped_for_time) or any(not f.done() for f in futures)
            for src in sorted(per_source, key=lambda x: -per_source[x]):
                say(LINE_SOURCE.format(place=place_name(src), n=per_source[src]))
            # A place that was asked and found nothing has its own line too
            # (otherwise a place asked 8 times is never named).
            named = {place_name(src) for src in per_source}
            for n in began:
                for kind in tasks[n][1]:
                    for label in _live_labels(wtl, "code-app" if kind == "subject" else kind):
                        place = _PLACE_SHORT.get(label, label)
                        if place not in named:
                            named.add(place)
                            say(LINE_NOTHING.format(place=place))
        if late:
            stopped = True
            say(LINE_STOPPED)
        for n in began:
            job, kinds, phrases = tasks[n]
            if kinds == ["subject"]:
                result["sends"].append({"phrase": phrases[0].phrase,
                                        "source": ", ".join(_live_labels(wtl, "code-app"))
                                        or "online search"})
                continue
            for kind in kinds:
                if not phrases:
                    break
                label = ", ".join(_live_labels(wtl, kind)) or "online search"
                for s in phrases:
                    row = {"phrase": s.phrase, "source": label}
                    if row not in result["sends"]:
                        result["sends"].append(row)
            if job["id"] not in reached:
                reached.add(job["id"])
        searched.extend(j["id"] for j in online_jobs if j["id"] in reached)
        result["not_reached"] = [j["id"] for j in online_jobs if j["id"] not in reached]
    # Finds in the order they were asked for (this computer first, then each
    # search in its turn), whichever search answered first: the plan reads the
    # same every run.
    with _SEARCH_LOCK:
        ordered = sorted(found, key=lambda fid: first_seen[fid])
        found = {fid: found[fid] for fid in ordered}
    # The reader's level (the age band the wizard kept): a young builder is
    # not offered books written for grown-ups (a space bot for a 10-year-old
    # was offered "Space-Time-Matter"). Set aside before scoring, so they never
    # take the place of a book at her level.
    reader = needs.get("reader")
    not_at_level = [found.pop(fid) for fid in list(found)
                    if found[fid].get("part") == "knowledge"
                    and not _at_reader_level(found[fid], reader)]
    for row in not_at_level:
        evidence["finds"].append({
            "id": str(row.get("id")), "source": row.get("source"), "part": "knowledge",
            "name": _clean(row.get("name"), 80), "what": _clean(row.get("what_it_is"), 160),
            "found_by": hits.get(str(row.get("id")), []), "mode": row.get("mode_guess"),
            "integrable": row.get("integrable"), "trust": row.get("trust"), "facts": {},
            "stage1": None,
            "meaning": {"job": None, "fit": None, "state": "dropped",
                        "why_dropped": meaning.NOT_AT_LEVEL}})
        result["left_out"] += 1

    mark("searched")
    say(LINE_CHECKING_START)
    if _judge_client(cfg) is not None:
        _warm_judge(cfg)
    if warm is not None:
        warm.finish()
    early_read = early.finish() if early is not None else None
    mark("looked")
    rows = list(found.values())
    for row in rows:
        _trust(row, tf)
        _live_trust(row)
    scored = meaning.score(rows, jobs, kind=kind_phrase, stage=1)
    mark("stage1")
    stage1 = {str(f["id"]): copy.deepcopy(f.get("meaning")) for f in scored}
    ahead = _judge_ahead(scored, jobs, cfg, stop_at=aim - JUDGE_RESERVE_S, kind=kind_phrase)
    closer = [found[str(f["id"])] for f in meaning.for_details(scored)]
    many = getattr(wtl, "details_many", None)
    if early_read is not None:
        read = list(early_read.values())
        # The looks during the search ran out of finds before their end time:
        # the best of the rest (filed by the first look at everything found)
        # are looked at too, in the time left.
        started = early.looked_at()
        rest = [f for f in closer if str(f.get("id")) not in started
                and meaning.closer_look_tier(f) <= 1]
        if rest and callable(many) and time.monotonic() < look_until:
            try:
                read += list(many(rest, budget=budget, deadline=look_until,
                                  workers=CLOSER_WORKERS))
            except Exception:  # noqa: BLE001 - no more looks, the blurbs stay
                pass
    elif callable(many) and closer:
        try:
            read = list(many(closer, budget=budget, deadline=look_until,
                             workers=CLOSER_WORKERS))
        except Exception:  # noqa: BLE001 - no closer look, the blurbs stay
            read = []
    else:
        read = []
        for rec in closer:
            if time.monotonic() > look_until:
                break
            try:
                read.append(wtl.details(rec, budget=budget))
            except Exception:  # noqa: BLE001 - no closer look, the blurb stays
                continue
    # The best fitting finds GitHub reports with no
    # licence are read at the pin too, last and only in the time left.
    unlicensed = _unreported_licence_looks(
        scored, found, done={str(g.get("id")) for g in read if isinstance(g, dict)}
        | (early.looked_at() if early is not None else set()))
    if unlicensed and callable(many) and time.monotonic() < look_until:
        try:
            read += list(many(unlicensed, budget=budget, deadline=look_until,
                              workers=CLOSER_WORKERS))
        except Exception:  # noqa: BLE001 - no more looks: they stay unlicensed
            pass
    for got in read:
        if isinstance(got, dict) and str(got.get("id")) in found:
            got = copy.deepcopy(got)
            got.pop("meaning", None)
            found[str(got["id"])] = got
    rows = list(found.values())
    for row in rows:
        _trust(row, tf)
        _live_trust(row)
    scored = meaning.score(rows, jobs, kind=kind_phrase, stage=2)
    mark("stage2")
    if ahead is not None:
        ahead.stop()
    ask = _judge_client(cfg)
    if ask is not None:
        meaning.judge(scored, jobs, ask, stop_at=aim - JUDGE_RESERVE_S, kind=kind_phrase,
                      ticks_stop_at=aim - JUDGE_TICKS_RESERVE_S)
        kept = getattr(ask, "kept", None)
        if callable(kept):
            # Answers the brain already gave ahead are read even after the stop
            # time: reading one asks nothing (a second look that ends late
            # would otherwise leave every answer given ahead unread).
            unanswered = [f for f in scored if not (f.get("meaning") or {}).get("judge")]
            meaning.judge(unanswered, jobs, kept, stop_at=None, kind=kind_phrase)
    # A brain that answered nothing at all (busy, or gone) is no judge: the
    # lines are shown as they would be without one.
    answered = any((f.get("meaning") or {}).get("judge") for f in scored)
    meaning.settle(scored, judged=answered)
    if ask is not None and answered:
        # Made for this job of THIS kind of bot? Asked about the lines that
        # could be called the best match, then the moderate ones: a "no"
        # leaves a moderate line out and no line is the best match without
        # a "yes" (so a COVID vaccine-slot checker is not the best match for
        # a pet's vaccines).
        meaning.purpose_check(scored, jobs, ask, kind=kind_phrase,
                              stop_at=aim - JUDGE_TICKS_RESERVE_S)
    mark("judged")

    job_by_id = {j["id"]: j for j in jobs}
    kept = 0
    got_for: dict[str, int] = {}
    fresh: dict[str, dict] = {}
    book_rows: list[dict] = []
    notes_of: dict[str, list[dict]] = {}
    for f in scored:
        m = f.get("meaning") or {}
        state, need = m.get("state"), m.get("job")
        evidence["finds"].append({
            "id": str(f.get("id")), "source": f.get("source"), "part": f.get("part"),
            "name": _clean(f.get("name"), 80), "what": _clean(f.get("what_it_is"), 160),
            "found_by": hits.get(str(f.get("id")), []), "mode": f.get("mode_guess"),
            "integrable": f.get("integrable"), "trust": f.get("trust"),
            "facts": {k: (f.get("repo") or {}).get(k) for k in
                      ("stars", "license_reported", "license_read", "language", "pushed_at",
                       "size_kb")} if isinstance(f.get("repo"), dict) else {},
            "stage1": stage1.get(str(f.get("id"))), "meaning": m})
        if state in ("ticked", "might_help") and need in job_by_id:
            if f.get("part") == "live-info" and meaning.is_catalog_row(f):
                # Not a line on the plan: a live-info find is an ingredient
                # of a job in the build plan (puffpai.build), made after the
                # owner's one Yes.
                continue
            if f.get("part") == "knowledge" and meaning.is_catalog_row(f):
                book_rows.append(f)
                continue
            if f.get("part") == "pictures" and meaning.is_catalog_row(f):
                item = _picture_item(f, job_by_id[need])
            else:
                item = _item(f, job_by_id[need])
            if not item["id"]:
                continue
            result["items"].append(item)
            fresh[item["id"]] = f
            kept += 1
            got_for[need] = got_for.get(need, 0) + 1
        elif state == "note" and need in job_by_id:
            notes_of.setdefault(need, []).append(f)
            got_for[need] = got_for.get(need, 0) + 1
        else:
            result["left_out"] += 1
    def best_first(rows: list[dict], cap: int) -> list[dict]:
        rows = sorted(rows, key=lambda f: -((f.get("meaning") or {}).get("fit") or 0.0))
        result["left_out"] += max(0, len(rows) - cap)
        return rows[:cap]
    for item in _book_items(best_first(book_rows, MAX_BOOKS), job_by_id, online):
        result["items"].append(item)
        kept += 1
        got_for[item["need"]] = got_for.get(item["need"], 0) + 1
    for job in jobs:
        if picture_job(job):
            result["items"].extend(_own_picture_items(job))
            got_for[job["id"]] = got_for.get(job["id"], 0) + 2
    by_ref = {str(f.get("id")): f for f in scored}
    row_of = {item["id"]: fresh.get(item["id"]) or by_ref.get(
        str((item.get("apply") or {}).get("ref"))) for item in result["items"]}
    gone = _other_kinds(result["items"], row_of, kind_phrase)
    gone += _cannot_be_used(result["items"], row_of)
    gone += _same_pictures(result["items"])
    _guard_ticks(result["items"], row_of, kind_phrase, jobs)
    gone += _same_kind(result["items"], row_of)
    gone += _only_brain_jobs(result["items"], row_of, jobs, kind_phrase)
    if gone:
        result["left_out"] += gone
        kept -= gone
        got_for = {}
        for item in result["items"]:
            got_for[item["need"]] = got_for.get(item["need"], 0) + 1
        for need in notes_of:
            got_for[need] = got_for.get(need, 0) + 1
    for need, rows_here in notes_of.items():
        result["notes"].setdefault(need, []).extend(_notes(rows_here))
    for jid in searched:
        if not got_for.get(jid) and jid not in result["not_reached"]:
            result["notes"].setdefault(jid, []).append(NOTHING_FOUND)
    _mark_best(result["items"], row_of, jobs, kind_phrase)
    try:
        result["similar"] = _similar(scored, jobs, kind_phrase)
    except Exception:  # noqa: BLE001 - evidence is a bonus, never a failure
        result["similar"] = []
    say(LINE_CHECKING.format(kept=kept, left=result["left_out"]))
    with _LOCK:
        _FOUND.update(copy.deepcopy(fresh))
    return finish()


__all__ = ["LINE_CHECKING", "LINE_LOCAL", "LINE_STOPPED", "NOTHING_FOUND", "NOT_IN_BUILD",
           "PALETTE", "SEARCH_DEADLINE_S", "found_for", "last_evidence", "picture_job",
           "plan_items"]
