"""Codeberg: project search, version pins and file reads (a small, polite source).

Codeberg (codeberg.org, a non-profit Forgejo host) keeps projects that are not
on GitHub. Its search matches one word well and several words only as an exact
phrase (measured: one topic word gave 63 projects, the same word
with its role noun gave 1), so each call carries one word. At most 2 searches a build (budget
pool ``codeberg``): the subject's topic words first, then the first code job's
first word when one call is left.

* search: ``https://codeberg.org/api/v1/repos/search?q=<one word>&includeDesc=true&limit=50&sort=stars&order=desc``;
* the version pin: ``https://codeberg.org/<o>/<r>.git/info/refs?service=git-upload-pack``
  (first 64 KB, the default branch's commit; no fallback call: not found means
  not pinned, and an unpinned find is never integrable);
* files at the pin: ``https://codeberg.org/<o>/<r>/raw/commit/<sha>/<path>``, read
  by the same reader, licence reader and mode rules as GitHub (``github.read_at_pin``).

Codeberg's robots.txt asks crawlers to keep off ``/api/`` and ``/raw/``. BuildBot
is not a crawler: it asks only after the owner presses "Start looking", for
her build, a handful of times (the registry row says ``on_request_only``, as
GitHub's rows do). Mirrors are skipped: their facts belong to the original.

Search words come only from ``outbound_query.Sendable`` phrases, put into the
address only with ``outbound_query.url_quote``. Everything read back is data.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import contextlib
import json
import re
from typing import Any, Callable, Optional

from puffpai import artifact_review
from puffpai.catalogs import searchers as S
from puffpai.catalogs.searchers import github as G

HOST = "codeberg.org"
SERVICE = "code_search"
PER_PAGE = 50                  # the most one Forgejo search answer gives
SEARCH_MAX_BYTES = 1024 * 1024
#: The longest one search may wait for Codeberg's answer. Measured: Codeberg's
#: search often took the full 30 s request time
#: and answered nothing, holding a job's whole search that long; a healthy
#: answer comes in a few seconds.
SEARCH_TIMEOUT_S = 10.0
POOL_SEARCH = "codeberg"
POOL_GIT = "codeberg_git"
POOL_RAW = "codeberg_raw"
POOL_ZIP = "codeberg_zip"
#: Words per build: the subject's topic words take the first calls.
SUBJECT_WORDS_MAX = 2

_WORD_RE = re.compile(r"^[a-z0-9]{2,40}$")


def _optional(modname: str):
    return S._optional(modname)


#: Forgejo user and project names: letters, digits, "-", "_" and "." (not first,
#: not last, never ".."). Wider than GitHub's owner rule, still safe in a path.
OWNER_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,38}[A-Za-z0-9])?$")


def repo_ok(owner: Any, name: Any) -> bool:
    return (isinstance(owner, str) and bool(OWNER_RE.match(owner)) and ".." not in owner
            and isinstance(name, str) and bool(G.REPO_RE.match(name))
            and name not in (".", "..") and ".." not in name
            and not name.lower().endswith(".git"))


def refs_url(owner: str, name: str) -> str:
    if not repo_ok(owner, name):
        raise ValueError("not a Codeberg project")
    return f"https://{HOST}/{owner}/{name}.git/info/refs?service=git-upload-pack"


def raw_url(owner: str, name: str, sha: str, path: str) -> str:
    import urllib.parse
    if not repo_ok(owner, name) or not G.SHA_RE.match(sha or ""):
        raise ValueError("not a Codeberg project at a pinned commit")
    return f"https://{HOST}/{owner}/{name}/raw/commit/{sha}/{urllib.parse.quote(path, safe='/')}"


def archive_url(found: dict[str, Any]) -> Optional[str]:
    """The download address at the pinned commit (used by Toolsmith), or None."""
    repo = found.get("repo") if isinstance(found, dict) else None
    if not isinstance(repo, dict) or repo.get("host") != HOST:
        return None
    owner, name, sha = repo.get("owner"), repo.get("name"), repo.get("pinned_commit")
    if not repo_ok(owner, name) or not isinstance(sha, str) or not G.SHA_RE.match(sha):
        return None
    return f"https://{HOST}/{owner}/{name}/archive/{sha}.tar.gz"


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------

def _license_reported(item: dict) -> Optional[str]:
    """Forgejo's ``licenses`` list, when it names exactly one licence (else None)."""
    lic = item.get("licenses")
    if isinstance(lic, list) and len(lic) == 1 and isinstance(lic[0], str) \
            and re.fullmatch(r"[A-Za-z0-9.+-]{1,60}", lic[0]):
        return lic[0]
    return None


def found_from_item(item: Any, *, part: str) -> Optional[dict[str, Any]]:
    """One search answer item as ``found/v1``, or None when it is malformed or not usable."""
    if not isinstance(item, dict):
        return None
    owner_rec = item.get("owner") if isinstance(item.get("owner"), dict) else {}
    owner, name = owner_rec.get("login") or owner_rec.get("username"), item.get("name")
    if not repo_ok(owner, name) or item.get("full_name") != f"{owner}/{name}":
        return None
    if item.get("private") is True or item.get("internal") is True or item.get("mirror") is True:
        return None          # private, or a copy whose facts belong to the original
    description = item.get("description") if isinstance(item.get("description"), str) else ""
    if artifact_review.reads_like_orders(G._URL_RE.sub(" ", description[:2000])) \
            or artifact_review.reads_like_orders(name):
        return None
    branch = item.get("default_branch")
    stars = item.get("stars_count")
    size = item.get("size")
    language = item.get("language") if isinstance(item.get("language"), str) \
        and G.LANG_RE.match(item["language"]) else None
    topics = [t for t in (item.get("topics") or []) if isinstance(t, str) and G.TOPIC_RE.match(t)][:20]
    changed = item.get("updated_at") if isinstance(item.get("updated_at"), str) else None
    repo = {
        "host": HOST, "owner": owner, "name": name,
        "default_branch": branch if G.branch_ok(branch) else None,
        "pinned_commit": None, "pinned_at": None,
        "stars": stars if isinstance(stars, int) and not isinstance(stars, bool) and stars >= 0 else 0,
        "pushed_at": changed[:10] if changed and re.match(r"^\d{4}-\d{2}-\d{2}", changed) else None,
        "archived": item.get("archived") is True, "fork": item.get("fork") is True,
        "license_reported": _license_reported(item), "license_read": None,
        "size_kb": size if isinstance(size, int) and not isinstance(size, bool) and size >= 0 else None,
        "language": language, "topics": topics, "latest_release": None,
        "in_awesome_selfhosted": False, "renamed_from": None,
    }
    url = f"https://{HOST}/{owner}/{name}"
    try:
        from puffpai.catalogs import trust_floor
        repo["in_awesome_selfhosted"] = bool(trust_floor.listed_lookup(url))
    except Exception:  # noqa: BLE001 - no snapshot: not listed
        repo["in_awesome_selfhosted"] = False
    return S.new_found(source="codeberg", part=part, id=f"codeberg:{owner}/{name}", name=name,
                       friendly=G._friendly(name), what_it_is=G._clean_text(description, S.WHAT_IT_IS_MAX),
                       url=url, repo=repo)


def words_for(job: dict, phrases: list, *, kind: str) -> list[str]:
    """The single words to search for *job* (already ``url_quote``-d, one per call)."""
    oq = _optional("puffpai.security.outbound_query")
    if oq is None:
        return []
    for p in phrases:
        if not oq.is_sendable(p):
            raise TypeError("Codeberg search takes only Sendable phrases")
    out: list[str] = []
    if kind == "subject":
        if job.get("id") != "subject" or not phrases:
            return []
        words = G._topic_words(oq.url_quote(phrases[0]))[:SUBJECT_WORDS_MAX]
    elif kind == "code-app":
        mine = G.phrases_for_kind(job, phrases, kind)
        words = G._core_words(oq.url_quote(mine[0]))[:1] if mine else []
    else:
        return []
    for w in words:
        if _WORD_RE.match(w) and w not in out:
            out.append(w)
    return out


def would_send(job: dict, phrases: list, *, kind: str, row: dict) -> bool:
    """Would :func:`search` ask Codeberg anything for these phrases? Sends nothing."""
    oq = _optional("puffpai.security.outbound_query")
    if oq is None or not all(oq.is_sendable(p) for p in phrases or []):
        return False
    return bool(words_for(job, list(phrases or []), kind=kind))


_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def whole_word_hit(item: Any, word: str) -> bool:
    """Does the searched *word* stand as a whole word (or its plural) in the
    item's name, description or topics?

    Codeberg's search matches inside words, so "chore" also finds
    "choreography", "hardware-anchored" and "nachorez" (those took three of
    the twenty places read closely). A name is
    read as words at its dashes, dots, underscores and capitals
    ("ChoreStrike", "chore-counter").
    """
    if not isinstance(item, dict) or not isinstance(word, str) or not word:
        return False
    name = _CAMEL_RE.sub(" ", str(item.get("name") or ""))
    topics = " ".join(t for t in item.get("topics") or [] if isinstance(t, str))
    desc = item.get("description") if isinstance(item.get("description"), str) else ""
    text = " ".join((name, desc[:2000], topics)).lower()
    pattern = r"(?<![a-z0-9])" + re.escape(word.lower()) + r"(?:s|es)?(?![a-z0-9])"
    return re.search(pattern, text) is not None


def _quick(seconds: float):
    """A shorter wait for the request on this thread (``source_fetch.quick_timeout``)."""
    from puffpai import source_fetch
    quick = getattr(source_fetch, "quick_timeout", None)
    return quick(seconds) if callable(quick) else contextlib.nullcontext()


def _run_search(word: str, *, part: str, budget, row: dict, progress) -> list[dict]:
    url = str(row.get("query_template") or "").format(words=word, per_page=PER_PAGE,
                                                        min_stars=G.MIN_STARS, since="")
    with _quick(SEARCH_TIMEOUT_S):
        got = S.fetch(url, hosts=[HOST], max_bytes=SEARCH_MAX_BYTES, service=SERVICE,
                      pool=POOL_SEARCH, budget=budget, progress=progress,
                      robots=row.get("robots") != "on_request_only",
                      accept="application/json", cache_key=("codeberg_search", url))
    if got.get("status") != 200 or not got.get("body"):
        return []
    try:
        data = json.loads(got["body"].decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        return []
    items = data.get("data") if isinstance(data, dict) else None
    out = []
    for item in (items if isinstance(items, list) else [])[:PER_PAGE]:
        if not whole_word_hit(item, word):
            continue          # Codeberg matches inside words: "chore" in "choreography"
        rec = found_from_item(item, part=part)
        if rec is not None:
            out.append(rec)
    return out


def search(job: dict, phrases: list, *, kind: str, budget, row: dict,
           progress: Optional[Callable[[str], None]] = None) -> list[dict]:
    """Registry adapter: ``found/v1`` records from Codeberg for *job* (one word a call)."""
    out: list[dict] = []
    seen: set[str] = set()
    for word in words_for(job, list(phrases or []), kind=kind):
        if budget.later(POOL_SEARCH) or budget.remaining(POOL_SEARCH) == 0:
            break
        for rec in _run_search(word, part="code-app", budget=budget, row=row, progress=progress):
            if rec["id"] not in seen:
                seen.add(rec["id"])
                out.append(rec)
    return out


# ---------------------------------------------------------------------------
# the version pin and the files
# ---------------------------------------------------------------------------

def tree_url(owner: str, name: str, sha: str) -> str:
    """One folder's listing at a commit or tree sha (Forgejo's git trees answer)."""
    return f"https://{HOST}/api/v1/repos/{owner}/{name}/git/trees/{sha}"


def zip_url(owner: str, name: str, sha: str) -> str:
    if not repo_ok(owner, name) or not G.SHA_RE.match(sha):
        raise ValueError("not a Codeberg project at a pinned commit")
    return f"https://{HOST}/{owner}/{name}/archive/{sha}.zip"


def pin(found: dict, *, budget, progress=None) -> Optional[str]:
    repo = found.get("repo") or {}
    owner, name, branch = repo.get("owner"), repo.get("name"), repo.get("default_branch")
    if repo.get("host") != HOST or not repo_ok(owner, name) \
            or (branch is not None and not G.branch_ok(branch)):
        return None
    got = S.fetch(refs_url(owner, name), hosts=[HOST], max_bytes=G.REFS_MAX_BYTES,
                  service=SERVICE, pool=POOL_GIT, budget=budget, progress=progress,
                  robots=False, allow_truncated=True, cache_key=("codeberg_refs", owner, name))
    if got.get("status") != 200:
        return None
    if branch is None:                   # a find from a list: HEAD names the branch
        sha, head_branch = G.head_from_refs(got.get("body") or b"")
        if sha and head_branch:
            repo["default_branch"] = head_branch
        return sha
    return G.sha_from_refs(got.get("body") or b"", branch)


def read_details(found: dict[str, Any], *, budget, progress=None) -> dict[str, Any]:
    """Fill the pin, the licence read and ``details`` for a Codeberg find (in place)."""
    repo = found.get("repo") or {}
    owner, name = repo.get("owner"), repo.get("name")
    if repo.get("host") != HOST or not repo_ok(owner, name):
        return found
    sha = pin(found, budget=budget, progress=progress)
    if not sha:
        return found
    # A small project is read as one archive at the pin, like GitHub's (one
    # request on this host instead of one per file); else file by file.
    archive = G.zip_reader(zip_url(owner, name, sha), host=HOST, pool=POOL_ZIP, owner=owner,
                           name=name, sha=sha, budget=budget,
                           progress=progress) if G.small_enough(repo) else None
    if archive is not None:
        return G.read_at_pin(found, archive, tree_counter=None)
    reader = G._Reader(owner, name, sha, budget, progress,
                       url_for=lambda path: raw_url(owner, name, sha, path), host=HOST,
                       pool=POOL_RAW, cache_prefix="codeberg_raw")
    # As on GitHub: read file by file, a licence
    # is confirmed only with a listing of the top at the pin (Forgejo's tree
    # answer has GitHub's shape). Codeberg's search reports a licence only
    # when it names exactly one, so "none reported" says nothing: every
    # project read this way is listed.
    reader.load_listing(lambda tree: tree_url(owner, name, tree), host=HOST, pool=POOL_GIT,
                        accept="application/json", cache_prefix="codeberg_top")
    return G.read_at_pin(found, reader, tree_counter=None,
                         probe_cap=None if G.small_enough(repo) else G.PROBES_MAX)


def details(found: dict[str, Any], *, budget, row: Optional[dict] = None,
            progress: Optional[Callable[[str], None]] = None) -> dict[str, Any]:
    """Registry adapter for the ``codeberg_details`` row."""
    return read_details(found, budget=budget, progress=progress)


__all__ = ["HOST", "archive_url", "details", "found_from_item", "pin", "raw_url",
           "read_details", "refs_url", "search", "words_for"]
