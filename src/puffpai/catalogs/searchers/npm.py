"""npm: command-line tools written in JavaScript (run in the locked box when added).

Search: ``https://registry.npmjs.org/-/v1/search?text=<words>&size=20`` (at
most 4 a build, budget pool ``npm``). A search answer is only a lead: every
fact the trust floor uses is read again in :func:`details`, never taken from
a record:

* the monthly downloads, from ``api.npmjs.org/downloads/point/last-month/<name>``;
* the linked GitHub project's own ``package.json`` (from raw.githubusercontent.com):
  its ``name`` must be the npm name (a package pointing at someone else's
  popular project, "starjacking", fails), and its ``bin`` says whether the
  package has a command. Only a package with a command can be added.

Search words come only from ``outbound_query.Sendable`` phrases, put into the
address only with ``outbound_query.url_quote``. Everything read back is data.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import json
import re
import urllib.parse
from typing import Any, Callable, Optional

from puffpai import artifact_review
from puffpai.catalogs import searchers as S
from puffpai.catalogs.searchers import github as G

REGISTRY_HOST = "registry.npmjs.org"
DOWNLOADS_HOST = "api.npmjs.org"
RAW_HOST = "raw.githubusercontent.com"
SERVICE = "code_search"
POOL = "npm"
PER_PAGE = 20
SEARCH_MAX_BYTES = 1024 * 1024
#: npm package names: lower case, optional @scope/, no path tricks.
NAME_RE = re.compile(r"^(?:@[a-z0-9][a-z0-9._-]{0,100}/)?[a-z0-9][a-z0-9._-]{0,213}$")
_REPO_RE = re.compile(r"^(?:git\+)?https://github\.com/([A-Za-z0-9-]{1,39})/([A-Za-z0-9_.-]{1,100}?)"
                      r"(?:\.git)?(?:/tree/([A-Za-z0-9._/-]{1,200}))?/?(?:#.*)?$")


def _optional(modname: str):
    return S._optional(modname)


def repo_of(link: Any) -> Optional[dict[str, str]]:
    """``{"owner", "name", "branch", "path"}`` for a GitHub repository link, else None."""
    m = _REPO_RE.match(str(link or "").strip())
    if not m or not G.repo_ok(m.group(1), m.group(2)):
        return None
    branch, path = "HEAD", ""
    if m.group(3):
        first, _, rest = m.group(3).partition("/")
        if not G.branch_ok(first) or ".." in rest.split("/"):
            return None
        branch, path = first, rest.strip("/")
    return {"owner": m.group(1), "name": m.group(2), "branch": branch, "path": path}


def found_from_object(obj: Any, *, part: str) -> Optional[dict[str, Any]]:
    if not isinstance(obj, dict) or not isinstance(obj.get("package"), dict):
        return None
    pkg = obj["package"]
    name = pkg.get("name")
    if not isinstance(name, str) or not NAME_RE.match(name):
        return None
    description = pkg.get("description") if isinstance(pkg.get("description"), str) else ""
    if artifact_review.reads_like_orders(G._URL_RE.sub(" ", description[:2000])):
        return None
    links = pkg.get("links") if isinstance(pkg.get("links"), dict) else {}
    link = repo_of(links.get("repository"))
    lic = pkg.get("license") if isinstance(pkg.get("license"), str) else None
    version = pkg.get("version") if isinstance(pkg.get("version"), str) \
        and re.fullmatch(r"[0-9A-Za-z.+-]{1,60}", pkg["version"]) else None
    date = pkg.get("date") if isinstance(pkg.get("date"), str) else None
    repo = None
    if link:
        repo = {"host": "github.com", "owner": link["owner"], "name": link["name"],
                "default_branch": None, "pinned_commit": None, "pinned_at": None, "stars": 0,
                "pushed_at": date[:10] if date and re.match(r"^\d{4}-\d{2}-\d{2}", date) else None,
                "archived": False, "fork": False,
                "license_reported": lic[:60] if lic else None, "license_read": None,
                "size_kb": None, "language": "JavaScript", "topics": [], "latest_release": version,
                "in_awesome_selfhosted": False, "renamed_from": None}
    return S.new_found(source="npm", part=part, id=f"npm:{name}", name=name,
                       friendly=G._friendly(name.rsplit("/", 1)[-1]),
                       what_it_is=G._clean_text(description, S.WHAT_IT_IS_MAX),
                       url=f"https://www.npmjs.com/package/{name}", repo=repo,
                       package={"registry": "npm", "name": name, "version": version,
                                "downloads_monthly": None, "bin": [],
                                "repo_name_matches": None})


def _query_words(job: dict, phrases: list, kind: str, oq) -> list[list[str]]:
    """The words of each query *search* sends (one query, the best phrase)."""
    if kind not in ("code-app", "cli"):
        return []
    out = []
    for p in G.phrases_for_kind(job, list(phrases or []), kind)[:1]:
        words = G._core_words(oq.url_quote(p))
        if words:
            out.append(words)
    return out


def would_send(job: dict, phrases: list, *, kind: str, row: dict) -> bool:
    """Would :func:`search` ask npm anything for these phrases? Sends nothing."""
    oq = _optional("puffpai.security.outbound_query")
    if oq is None or not all(oq.is_sendable(p) for p in phrases or []):
        return False
    return bool(_query_words(job, phrases, kind, oq))


def search(job: dict, phrases: list, *, kind: str, budget, row: dict,
           progress: Optional[Callable[[str], None]] = None) -> list[dict]:
    """Registry adapter: npm packages for *job*'s code or command-line phrases."""
    oq = _optional("puffpai.security.outbound_query")
    if oq is None or kind not in ("code-app", "cli"):
        return []
    for p in phrases or []:
        if not oq.is_sendable(p):
            raise TypeError("npm search takes only Sendable phrases")
    out: list[dict] = []
    seen: set[str] = set()
    for words in _query_words(job, phrases, kind, oq):
        if budget.later(POOL) or budget.remaining(POOL) == 0:
            break
        url = str(row.get("query_template") or "").format(words="+".join(words),
                                                            per_page=PER_PAGE, min_stars=0,
                                                            since="")
        got = S.fetch(url, hosts=[REGISTRY_HOST], max_bytes=SEARCH_MAX_BYTES, service=SERVICE,
                      pool=POOL, budget=budget, progress=progress,
                      robots=row.get("robots") != "on_request_only",
                      accept="application/json", cache_key=("npm_search", url))
        if got.get("status") != 200 or not got.get("body"):
            continue
        try:
            data = json.loads(got["body"].decode("utf-8"))
        except (UnicodeDecodeError, ValueError, RecursionError):
            continue
        for obj in ((data.get("objects") or []) if isinstance(data, dict) else [])[:PER_PAGE]:
            rec = found_from_object(obj, part=kind)
            if rec is not None and rec["id"] not in seen:
                seen.add(rec["id"])
                out.append(rec)
    return out


def _downloads(name: str, *, budget, progress) -> Optional[int]:
    url = f"https://{DOWNLOADS_HOST}/downloads/point/last-month/{urllib.parse.quote(name, safe='@/')}"
    got = S.fetch(url, hosts=[DOWNLOADS_HOST], max_bytes=4096, service=SERVICE,
                  pool="npm_downloads", budget=budget, progress=progress, robots=False,
                  accept="application/json", cache_key=("npm_downloads", name))
    if got.get("status") != 200 or not got.get("body"):
        return None
    try:
        data = json.loads(got["body"].decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        return None
    n = data.get("downloads") if isinstance(data, dict) else None
    return n if isinstance(n, int) and not isinstance(n, bool) and n >= 0 \
        and data.get("package") == name else None


def _repo_package_json(link: dict[str, str], *, budget, progress) -> Optional[dict]:
    path = f"{link['path']}/package.json" if link["path"] else "package.json"
    url = (f"https://{RAW_HOST}/{link['owner']}/{link['name']}/"
           f"{urllib.parse.quote(link['branch'], safe='')}/{urllib.parse.quote(path, safe='/')}")
    got = S.fetch(url, hosts=[RAW_HOST], max_bytes=256 * 1024, service=SERVICE, pool="npm_raw",
                  budget=budget, progress=progress, robots=False,
                  cache_key=("npm_repo_pkg", link["owner"], link["name"], link["branch"], path))
    if got.get("status") != 200 or not got.get("body"):
        return None
    try:
        data = json.loads(got["body"].decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        return None
    return data if isinstance(data, dict) else None


def read_details(found: dict[str, Any], *, budget, progress=None) -> dict[str, Any]:
    """Re-derive every npm fact the trust floor uses (in place)."""
    pkg = found.get("package") if isinstance(found.get("package"), dict) else None
    name = pkg.get("name") if pkg else None
    if pkg is None or not isinstance(name, str) or not NAME_RE.match(name) \
            or found.get("id") != f"npm:{name}":
        return found
    pkg.update(downloads_monthly=None, bin=[], repo_name_matches=None)
    pkg["downloads_monthly"] = _downloads(name, budget=budget, progress=progress)
    repo = found.get("repo") if isinstance(found.get("repo"), dict) else None
    link = None
    if repo and repo.get("host") == "github.com" and G.repo_ok(repo.get("owner"), repo.get("name")):
        link = repo_of(f"https://github.com/{repo['owner']}/{repo['name']}")
    if link is None:
        return found
    data = _repo_package_json(link, budget=budget, progress=progress)
    if data is None:
        return found
    pkg["repo_name_matches"] = data.get("name") == name
    b = data.get("bin")
    if isinstance(b, str) and b:
        pkg["bin"] = [name.rsplit("/", 1)[-1]]
    elif isinstance(b, dict):
        pkg["bin"] = [k for k in list(b)[:10] if isinstance(k, str) and NAME_RE.match(k)]
    return found


def details(found: dict[str, Any], *, budget, row: Optional[dict] = None,
            progress: Optional[Callable[[str], None]] = None) -> dict[str, Any]:
    """Registry adapter for the ``npm_details`` row."""
    return read_details(found, budget=budget, progress=progress)


__all__ = ["details", "found_from_object", "read_details", "repo_of", "search", "would_send"]
