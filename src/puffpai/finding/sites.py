"""One small search per site, each ranked the way the site itself ranks.

No hiding filters: no star floor, no licence
filter, no "is this a page about the topic" check, no meaning re-rank. Each
site's own order is kept, and every find is labelled with its type, its
numbers, when it last changed, its licence and the shelf's own safety check
where the shelf has one.

Sites, and how each ranks:

* **ClawHub** ``GET clawhub.ai/api/v1/search?q=`` (meaning + exact-word boosts
  + a small popularity bump, its own order). Its own check comes from
  ``/api/v1/skills/<slug>/verify`` (:func:`clawhub_verify`).
* **skills.sh** ``GET skills.sh/api/search?q=`` (its own order, by installs).
* **the Hermes skill list** ``skills-index.json`` (read once a day), matched
  the way Hermes matches it (:mod:`.hermes_index`).
* **GitHub** ``GET api.github.com/search/repositories?q=`` (best match).
* **Codeberg** ``GET codeberg.org/api/v1/repos/search?q=`` (its default order).
* **npm** ``GET registry.npmjs.org/-/v1/search?text=`` (its search score).
* **PyPI**: PyPI has no search a program may use, so the exact names made
  from the words are looked up (``pypi.org/pypi/<name>/json``).
* **awesome-selfhosted**: the app list (:mod:`.app_catalog`: the copy saved
  with this build, refreshed weekly through the Bridge): self-hostable apps
  matched by meaning ("an app for <job>") and by words; nothing is sent.
* **the MCP Registry** ``GET registry.modelcontextprotocol.io/v0/servers?search=``
  (it matches server names, so the words are asked one at a time).

Host names come from the searchers in :mod:`puffpai.catalogs.searchers`.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import re
import urllib.parse
from typing import Any, Optional

from puffpai.finding import net
from puffpai.finding import wording as W

CLAWHUB = "clawhub.ai"
SKILLS_SH = "skills.sh"
GITHUB_API = "api.github.com"
GITHUB_RAW = "raw.githubusercontent.com"
CODEBERG = "codeberg.org"
NPM = "registry.npmjs.org"
PYPI = "pypi.org"
MCP_REGISTRY = "registry.modelcontextprotocol.io"

#: Which outside switch each site's searches go under.
SERVICE = {
    "clawhub": "shelf_clawhub", "skills.sh": "shelf_skills_sh", "hermes": "shelf_hermes",
    "mcp-registry": "shelf_mcp_registry", "github": "code_search",
    "codeberg": "code_search", "npm": "code_search", "pypi": "code_search",
    "awesome-selfhosted": "",
    # each library has its own row on What leaves your computer, read by
    # the library's own fetcher (puffpai.finding.books)
    "libraries": "",
}

PER_SITE = 10
WHAT_MAX = 160

_TAG_RE = re.compile(r"<[^>]+>")
_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]")


def plain(value: Any, limit: int = WHAT_MAX) -> str:
    """Text from a site, made safe to show: no tags, no control characters,
    no long dashes, cut on a word."""
    text = _TAG_RE.sub(" ", str(value or ""))
    text = _CTRL_RE.sub(" ", text)
    text = re.sub(r"\s*[‒-―⸺⸻﹘﹣－]+\s*", ", ", text)
    text = " ".join(text.split())
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0].rstrip(",;:") + "..."
    return text


def _q(words: str) -> str:
    return urllib.parse.quote(" ".join(str(words or "").split())[:100])


def make(*, site: str, type_: str, id: str, name: str, by: str = "", what: str = "",
         url: str = "", popularity: Optional[dict] = None, changed: Any = None,
         licence: Any = None, verdict: str = "", verdict_shelf: str = "",
         can: str = "look", look_why: str = "", rank: int = 0,
         raw: Optional[dict] = None) -> dict[str, Any]:
    """One find, with its plain words filled in."""
    pop = {k: v for k, v in (popularity or {}).items() if isinstance(v, int)}
    f: dict[str, Any] = {
        "id": id[:200], "site": site, "site_name": W.SITE_NAMES.get(site, site),
        "type": type_, "type_words": W.type_words(type_),
        "name": plain(name, 80) or id.rsplit("/", 1)[-1][:80], "by": plain(by, 60),
        "what": plain(what), "url": str(url or "")[:300],
        "popularity": pop, "popularity_words": W.popularity_words(pop),
        "changed_words": W.month_words(changed),
        "licence": str(licence or "")[:60], "licence_words": W.licence_words(licence),
        "no_licence": no_licence(licence),
        "verdict": str(verdict or "").lower()[:20],
        "verdict_words": W.verdict_words(verdict_shelf or W.SITE_NAMES.get(site, site),
                                         verdict),
        "can": can, "can_words": W.can_words(can, type_, look_why),
        "rank": int(rank), "raw": raw or {},
    }
    f["can_trade"] = W.can_trade(f["name"], f["what"])
    # The human language its own words are in (from its description; a
    # GitHub preview reads the README too). Ranking prefers hers.
    f["words_language"] = words_language(what)
    f["line"] = W.line_for(f)
    return f


# ── skill shelves ───────────────────────────────────────────────────────────

def _clawhub_owner(item: dict) -> str:
    owner = item.get("ownerHandle")
    if not owner and isinstance(item.get("owner"), dict):
        owner = item["owner"].get("handle")
    return str(owner or "")


def clawhub(words: str, limit: int = PER_SITE) -> list[dict]:
    """ClawHub's own semantic search, in its own order."""
    url = f"https://{CLAWHUB}/api/v1/search?q={_q(words)}&limit={int(limit)}"
    ans = net.get(url, hosts=[CLAWHUB], service=SERVICE["clawhub"])
    data = ans.json() if ans.ok else None
    out: list[dict] = []
    for i, item in enumerate((data or {}).get("results") or []):
        if not isinstance(item, dict) or not item.get("slug"):
            continue
        slug = str(item["slug"])
        owner = _clawhub_owner(item)
        source = str(item.get("source") or "clawhub")
        install = item.get("install") if isinstance(item.get("install"), dict) else {}
        trust = item.get("trust") if isinstance(item.get("trust"), dict) else {}
        verdict = str(trust.get("clawHubVerdict") or "")
        shelf = "ClawHub"
        upstream = trust.get("upstreamScanners") if isinstance(
            trust.get("upstreamScanners"), dict) else {}
        if not verdict and upstream:
            states = [str((v or {}).get("status") or "") for v in upstream.values()
                      if isinstance(v, dict)]
            if "malicious" in states:
                verdict = "malicious"
            elif any(s in ("fail", "warn", "suspicious") for s in states):
                verdict = "suspicious"
            elif states and all(s == "pass" for s in states):
                verdict = "pass"
            shelf = "skills.sh"
        native = item.get("native") if isinstance(item.get("native"), dict) else {}
        skill = native.get("skill") if isinstance(native.get("skill"), dict) else {}
        if not verdict and skill.get("isSuspicious") is True:
            verdict = "suspicious"
        if source == "skills-sh":
            ref = str(install.get("reference") or "").removeprefix("skills-sh:")
            fid = f"skills.sh:{ref}" if ref.count("/") == 2 else f"clawhub:{owner}/{slug}"
        else:
            fid = f"clawhub:{owner}/{slug}" if owner else f"clawhub:{slug}"
        installs = None
        sid = item.get("sourceIdentity") if isinstance(item.get("sourceIdentity"), dict) else {}
        if isinstance(sid.get("lifetimeInstalls"), int):
            installs = sid["lifetimeInstalls"]
        pop = {"installs": installs} if installs is not None else \
            {"downloads": item.get("downloads") if isinstance(item.get("downloads"), int)
             else (skill.get("stats") or {}).get("downloads")}
        out.append(make(
            site="clawhub", type_="skill", id=fid, name=item.get("displayName") or slug,
            by=owner, what=item.get("summary"),
            url=f"https://{CLAWHUB}/{owner}/skills/{slug}" if owner else f"https://{CLAWHUB}",
            popularity=pop, changed=item.get("updatedAt"),
            licence="MIT-0" if source == "clawhub" else "",
            verdict=verdict, verdict_shelf=shelf, can="install", rank=i,
            raw={"slug": slug, "owner": owner, "source": source,
                 "reference": str(install.get("reference") or "")}))
    return out


def clawhub_verify(slug: str, owner: str = "") -> Optional[dict]:
    """ClawHub's Skill Card check for one skill (``/verify``), or None.

    ``{"decision", "verdict", "summary", "version", "files": [{"path","size","sha256"}]}``.
    Never cached for long: the verdict can change."""
    q = f"?ownerHandle={urllib.parse.quote(owner)}" if owner else ""
    url = f"https://{CLAWHUB}/api/v1/skills/{urllib.parse.quote(slug)}/verify{q}"
    ans = net.get(url, hosts=[CLAWHUB], service=SERVICE["clawhub"], cache_s=600)
    data = ans.json() if ans.ok else None
    if not isinstance(data, dict):
        return None
    sec = data.get("security") if isinstance(data.get("security"), dict) else {}
    art = data.get("artifact") if isinstance(data.get("artifact"), dict) else {}
    files = [{"path": str(f.get("path") or ""), "size": f.get("size"),
              "sha256": str(f.get("sha256") or "")}
             for f in (art.get("files") or []) if isinstance(f, dict)]
    reasons = [str(r) for r in (data.get("reasons") or [])]
    verdict = str(sec.get("verdict") or sec.get("status") or "")
    if any("malicious" in r for r in reasons) or str(sec.get("status")) == "malicious":
        verdict = "malicious"
    return {"decision": str(data.get("decision") or ""), "verdict": verdict.lower(),
            "summary": plain(sec.get("summary"), 400), "version": str(data.get("version") or ""),
            "files": files, "page": str(data.get("pageUrl") or "")}


def skills_sh(words: str, limit: int = PER_SITE) -> list[dict]:
    """skills.sh's own search (by installs). Skills live in GitHub projects."""
    url = f"https://{SKILLS_SH}/api/search?q={_q(words)}&limit={int(limit)}"
    ans = net.get(url, hosts=[SKILLS_SH], service=SERVICE["skills.sh"])
    data = ans.json() if ans.ok else None
    out: list[dict] = []
    for i, item in enumerate((data or {}).get("skills") or []):
        if not isinstance(item, dict):
            continue
        ident = str(item.get("id") or "")
        source = str(item.get("source") or "")
        if ident.count("/") != 2 or source.count("/") != 1:
            continue          # not a GitHub owner/repo/skill
        owner, repo = source.split("/")
        skill_id = str(item.get("skillId") or ident.rsplit("/", 1)[-1])
        out.append(make(
            site="skills.sh", type_="skill", id=f"skills.sh:{ident}",
            name=item.get("name") or skill_id, by=owner,
            what=f"A skill in the {source} project on GitHub.",
            url=f"https://{SKILLS_SH}/{ident}",
            popularity={"installs": item.get("installs")}, can="install", rank=i,
            raw={"repo": source, "skill": skill_id}))
    return out


# ── apps and code ───────────────────────────────────────────────────────────

_MCP_TOPICS = {"mcp", "mcp-server", "model-context-protocol", "modelcontextprotocol",
               "mcp-servers"}
_SKILL_TOPICS = {"agent-skills", "claude-skills", "agent-skill", "claude-skill", "skills",
                 "skill-md", "agentskills"}
_SERVER_TOPICS = {"self-hosted", "selfhosted", "docker", "homelab", "server", "web-server",
                  "docker-compose", "home-server"}
_CLI_TOPICS = {"cli", "command-line", "terminal", "command-line-tool", "tui", "shell"}
_DATA_TOPICS = {"dataset", "datasets", "data", "csv", "template", "templates",
                "awesome", "awesome-list", "list", "spreadsheet", "checklist", "json-data"}
_LIB_TOPICS = {"library", "sdk", "api-client", "api-wrapper", "package", "framework",
               "module", "npm-package", "python-library"}
#: Words saying a project has a screen of its own (it is an app).
_SCREEN_WORDS = {"dashboard", "webapp", "web-app", "self-hosted", "selfhosted", "frontend",
                 "gui", "reader", "viewer", "website", "web-ui", "webui", "pwa"}
_PAGE_LANGUAGES = {"HTML","CSS", "JavaScript", "TypeScript", "Vue", "Svelte", "Astro"}
_SERVER_LANGUAGES = {"PHP", "Ruby", "Java", "C#", "Elixir", "Python", "Go", "Kotlin"}


def repo_type(name: str, description: str, topics: list, language: Any) -> str:
    """The type of a code project, from what the site says about it (no download)."""
    tops = {str(t).lower() for t in topics or []}
    words = set(re.split(r"[^a-z0-9]+", f"{name} {description}".lower()))
    # An app with a screen that also offers a tool server (an MCP endpoint
    # beside its dashboard) is an app: the tool server is an extra.
    has_screen = bool((tops | words) & _SCREEN_WORDS)
    if (tops & _MCP_TOPICS or "mcp" in words) and not has_screen:
        return "mcp"
    if tops & _SKILL_TOPICS or {"skill", "skills"} & words and "agent" in words:
        return "skill"
    if tops & _SERVER_TOPICS or {"self-hosted", "selfhosted"} & words:
        return "server_app"
    if tops & _CLI_TOPICS or {"cli"} & words:
        return "cli"
    if tops & _DATA_TOPICS or not language:
        return "data"
    if tops & _LIB_TOPICS or {"library", "sdk"} & words:
        return "library"
    if str(language) in _PAGE_LANGUAGES:
        return "browser_app"
    if str(language) in _SERVER_LANGUAGES:
        return "server_app"
    return "cli"


def _code_can(type_: str, licence: Any = "x") -> tuple[str, str]:
    """What BuildBot can do with a code project of this type: (can, why).
    Every code project can be added (Toolsmith runs it as an app, or makes an
    app from it, after her Yes). No licence never blocks: *why* is the plain
    line that using it is her call. Skills and tool servers keep their own
    ways in (the skill shelves, Add a tool server)."""
    if type_ == "skill":
        return "look", W.LOOK_WHY["skill"]
    if type_ == "mcp":
        return "look", W.LOOK_WHY["mcp"]
    return "integrate", W.NO_LICENCE_LINE if no_licence(licence) else ""


def no_licence(licence: Any) -> bool:
    """Its maker gave no licence at all (an unrecognised one is still a licence)."""
    return str(licence or "").strip().lower() in ("", "none", "null")


def _github_search(url: str) -> "net.Answer":
    """One GitHub search: with her own account when it is connected, the vault
    is open and GitHub is switched on (``github_account.search_key_id``),
    else unauthenticated as always. GitHub's rate limit is respected: while it
    says none are left, nothing is sent."""
    from puffpai import github_account
    if github_account.rate_blocked("search"):
        net._note("rate_limited")  # noqa: SLF001 - same package
        return net.Answer(status=None, body=None, headers={}, reason="rate_limited",
                          cached=False)
    key = github_account.search_key_id()
    ans = net.get(url, hosts=[GITHUB_API], service=SERVICE["github"],
                  accept="application/vnd.github+json",
                  **({"key_id": key} if key else {}))
    if key and ans.get("reason") in ("key_unavailable", "key_host"):
        # The key could not be used after all (the vault locked a moment
        # ago): the search goes unauthenticated, as it would have anyway.
        ans = net.get(url, hosts=[GITHUB_API], service=SERVICE["github"],
                      accept="application/vnd.github+json")
    github_account.note_rate(ans.get("headers"), ans.get("status"), "search")
    return ans


def github(words: str, limit: int = PER_SITE) -> list[dict]:
    """GitHub's best-match project search, no qualifiers added."""
    q = urllib.parse.quote_plus(" ".join(str(words or "").split())[:100])
    url = f"https://{GITHUB_API}/search/repositories?q={q}&per_page={int(limit)}"
    ans = _github_search(url)
    data = ans.json() if ans.ok else None
    out: list[dict] = []
    for i, item in enumerate((data or {}).get("items") or []):
        if not isinstance(item, dict) or not item.get("full_name"):
            continue
        if item.get("private") is True:
            # A signed-in search can see her private projects; those join a
            # build only through her own list, with their own setup row.
            continue
        lic = item.get("license") if isinstance(item.get("license"), dict) else {}
        type_ = repo_type(str(item.get("name") or ""), str(item.get("description") or ""),
                          item.get("topics") or [], item.get("language"))
        can, why = _code_can(type_, lic.get("spdx_id"))
        owner = (item.get("owner") or {}).get("login") if isinstance(item.get("owner"),
                                                                     dict) else ""
        out.append(make(
            site="github", type_=type_, id=f"github:{item['full_name']}",
            name=item.get("name") or item["full_name"], by=owner or "",
            what=(("No longer kept up (archived). " if item.get("archived") else "")
                  + str(item.get("description") or "")),
            url=str(item.get("html_url") or f"https://github.com/{item['full_name']}"),
            popularity={"stars": item.get("stargazers_count")},
            changed=item.get("pushed_at"), licence=lic.get("spdx_id"),
            can=can, look_why=why, rank=i, raw={"item": _github_item(item)}))
        out[-1]["origin"] = {"owner": plain(owner, 60),
                             "licence": str(lic.get("spdx_id") or "")[:60],
                             "code_language": plain(item.get("language"), 30)}
    return out


def _github_item(item: dict) -> dict:
    """The fields ``github.found_from_item`` reads, kept for adding it later."""
    keep = ("name", "full_name", "owner", "private", "description", "html_url", "homepage",
            "stargazers_count", "pushed_at", "archived", "fork", "license", "size",
            "language", "topics", "default_branch")
    out = {k: item.get(k) for k in keep}
    if isinstance(out.get("owner"), dict):
        out["owner"] = {"login": out["owner"].get("login")}
    return out


def codeberg(words: str, limit: int = PER_SITE) -> list[dict]:
    """Codeberg's own project search (its default order)."""
    url = (f"https://{CODEBERG}/api/v1/repos/search?q={_q(words)}"
           f"&includeDesc=true&limit={int(limit)}")
    ans = net.get(url, hosts=[CODEBERG], service=SERVICE["codeberg"])
    data = ans.json() if ans.ok else None
    out: list[dict] = []
    for i, item in enumerate((data or {}).get("data") or []):
        if not isinstance(item, dict) or not item.get("full_name"):
            continue
        type_ = repo_type(str(item.get("name") or ""), str(item.get("description") or ""),
                          item.get("topics") or [], item.get("language"))
        owner = (item.get("owner") or {}).get("login") if isinstance(item.get("owner"),
                                                                     dict) else ""
        lic = (item.get("licenses") or [None])[0] if isinstance(item.get("licenses"),
                                                                 list) else None
        can, why = _code_can(type_, lic)
        out.append(make(
            site="codeberg", type_=type_, id=f"codeberg:{item['full_name']}",
            name=item.get("name") or item["full_name"], by=owner or "",
            what=item.get("description"),
            url=str(item.get("html_url") or f"https://codeberg.org/{item['full_name']}"),
            popularity={"stars": item.get("stars_count")}, changed=item.get("updated_at"),
            licence=lic, can=can, look_why=why, rank=i, raw={"item": item}))
    return out


def _package_type(name: str, description: str, keywords: list) -> str:
    keys = {str(k).lower() for k in keywords or []}
    words = set(re.split(r"[^a-z0-9]+", f"{name} {description}".lower()))
    if keys & _MCP_TOPICS or "mcp" in words or "modelcontextprotocol" in words:
        return "mcp"
    if keys & _CLI_TOPICS or "cli" in words:
        return "cli"
    return "library"


_MENTION_STOP = frozenset("a an the and or for to of in on with my your our app apps "
                          "tool tools bot bots helper manager".split())


def _mentions(words: str, *texts: Any) -> bool:
    """True when the package's own name, description or keywords hold at
    least one of the search words (or the words name nothing to look for)."""
    want = {w for w in re.split(r"[^a-z0-9]+", str(words or "").lower())
            if len(w) > 2 and w not in _MENTION_STOP}
    if not want:
        return True
    text = " ".join(" ".join(map(str, t)) if isinstance(t, list) else str(t or "")
                    for t in texts).lower()
    def stem(word: str) -> str:
        return word[:-1] if len(word) > 4 and word.endswith("s") else word
    have = {stem(w) for w in re.split(r"[^a-z0-9]+", text) if w}
    return any(stem(w) in have for w in want)


def npm(words: str, limit: int = PER_SITE) -> list[dict]:
    """npm's own search score order."""
    url = f"https://{NPM}/-/v1/search?text={_q(words)}&size={int(limit)}"
    ans = net.get(url, hosts=[NPM], service=SERVICE["npm"])
    data = ans.json() if ans.ok else None
    out: list[dict] = []
    for i, obj in enumerate((data or {}).get("objects") or []):
        pkg = obj.get("package") if isinstance(obj, dict) else None
        if not isinstance(pkg, dict) or not pkg.get("name"):
            continue
        name = str(pkg["name"])
        if not _mentions(words, name, pkg.get("description"), pkg.get("keywords")):
            continue          # npm's popularity bump lifts packages about nothing she said
        type_ = _package_type(name, str(pkg.get("description") or ""), pkg.get("keywords"))
        dl = obj.get("downloads") if isinstance(obj.get("downloads"), dict) else {}
        can, why = ("add_mcp", "") if type_ == "mcp" else ("look", "")
        pub = pkg.get("publisher") if isinstance(pkg.get("publisher"), dict) else {}
        out.append(make(
            site="npm", type_=type_, id=f"npm:{name}", name=name,
            by=str(pub.get("username") or ""), what=pkg.get("description"),
            url=f"https://www.npmjs.com/package/{name}",
            popularity={"monthly_downloads": dl.get("monthly")},
            changed=pkg.get("date"), licence=pkg.get("license"), can=can, look_why=why,
            rank=i, raw={"name": name, "version": str(pkg.get("version") or "")}))
    return out


def pypi_names(words: str) -> list[str]:
    """The exact project names PyPI is asked for: the words joined by "-", and
    the single longest word."""
    ws = [w for w in re.split(r"[^a-z0-9]+", str(words or "").lower()) if w]
    names: list[str] = []
    if len(ws) > 1:
        names.append("-".join(ws[:4]))
    if ws:
        names.append(max(ws, key=len))
    return list(dict.fromkeys(names))[:2]


def pypi(words: str, limit: int = PER_SITE) -> list[dict]:
    out: list[dict] = []
    for i, name in enumerate(pypi_names(words)):
        url = f"https://{PYPI}/pypi/{urllib.parse.quote(name)}/json"
        ans = net.get(url, hosts=[PYPI], service=SERVICE["pypi"])
        data = ans.json() if ans.ok else None
        info = (data or {}).get("info") if isinstance(data, dict) else None
        if not isinstance(info, dict) or not info.get("name"):
            continue
        classifiers = [str(c) for c in info.get("classifiers") or []]
        kw = re.split(r"[,\s]+", str(info.get("keywords") or ""))
        type_ = _package_type(str(info["name"]), str(info.get("summary") or ""), kw)
        if type_ == "library" and any("Environment :: Console" in c for c in classifiers):
            type_ = "cli"
        lic = info.get("license_expression") or (
            info.get("license") if len(str(info.get("license") or "")) < 40 else "")
        files = (data or {}).get("urls") or []
        when = files[0].get("upload_time_iso_8601") if files and isinstance(files[0], dict) \
            else None
        can = "add_mcp" if type_ == "mcp" else "look"
        out.append(make(
            site="pypi", type_=type_, id=f"pypi:{info['name']}", name=str(info["name"]),
            by=str(info.get("author") or ""), what=info.get("summary"),
            url=f"https://pypi.org/project/{info['name']}/", changed=when, licence=lic,
            can=can, rank=i, raw={"name": str(info["name"]),
                                  "version": str(info.get("version") or "")}))
    return out[:limit]


#: The first ask for every job: a self-hostable app that does it (app
#: first). Every row of the list is self-hostable, so
#: the meaning query says only "an app for": with "self-hostable" in it the
#: hosting platforms (Sandstorm, Tipi) came first for every job (measured).
APP_QUERY = "an app for {words}"
#: How close in meaning a listed app must be to be shown at all (decide
#: has its own, higher bar).
APP_MEANING_MIN = 0.35
#: Listed rows that are only a way to host other programs, never a job.
_HOSTING_ONLY = frozenset({"Self-hosting Solutions"})


def awesome_selfhosted(words: str, limit: int = PER_SITE) -> list[dict]:
    """The awesome-selfhosted list (the copy saved with this build, or the
    weekly fresh copy): apps that do the job, matched by meaning (the list's
    shipped vectors over name, description and tags) and by words. Nothing
    is sent for a search."""
    from puffpai.finding import app_catalog
    try:
        rows = app_catalog.rows()
    except Exception:  # noqa: BLE001 - no list in this build
        return []
    app_catalog.refresh_soon()
    by_id = {str(r.get("id")): r for r in rows}
    picked: list[dict] = []
    seen: set[str] = set()

    def keep(row: Optional[dict]) -> None:
        rid = str((row or {}).get("id") or "")
        if not row or rid in seen or row.get("nonfree") or row.get("archived") is True:
            return
        tags = set(row.get("tags") or [])
        if tags and tags <= _HOSTING_ONLY:
            return
        seen.add(rid)
        picked.append(row)

    try:
        for _sim, rid in app_catalog.meaning_match(APP_QUERY.format(words=" ".join(str(words).split())),
                                         limit=limit * 2, min_sim=APP_MEANING_MIN):
            keep(by_id.get(rid))
    except Exception:  # noqa: BLE001 - no reading helper: words only
        pass
    from puffpai.finding import hermes_index
    ranked = hermes_index.rank(
        rows, words, fields=lambda r: (str(r.get("name") or ""),
                                       str(r.get("description") or ""),
                                       [str(t) for t in r.get("tags") or []], ""))
    for row in ranked:
        keep(row)
    out: list[dict] = []
    for i, row in enumerate(picked[:limit]):
        url = str(row.get("source_code_url") or row.get("website_url") or "")
        lic = row.get("licenses")
        lic = lic[0] if isinstance(lic, list) and lic else lic
        out.append(make(
            site="awesome-selfhosted", type_="server_app",
            id=f"awesome-selfhosted:{str(row.get('id') or row.get('name')).split(':')[-1]}",
            name=str(row.get("name") or ""),
            what=row.get("description"), url=url,
            popularity={"stars": row.get("stars")},
            changed=row.get("updated_at"), licence=lic, can="look", rank=i,
            raw={"row_id": str(row.get("id") or "")}))
        # How it runs and what it is about, as the list says (classify reads
        # "platforms" to tell whether it can start on this computer).
        out[-1]["platforms"] = [str(p) for p in row.get("platforms") or []][:12]
        out[-1]["licences"] = [str(x)[:40] for x in (row.get("licenses") or [])
                               if isinstance(row.get("licenses"), list) and x][:4]
        out[-1]["tags"] = [str(t) for t in row.get("tags") or []][:8]
        if row.get("demo_url"):
            out[-1]["demo_url"] = str(row["demo_url"])[:300]
    return out


# ── where a find comes from, and what language its words are in ────────────
#
# An owner can install an app without knowing where it comes from or what
# language it is in. A GitHub find says who made it, where they say they are
# (only when their public profile shows it), its licence, its code language
# and the human language of its README. Code from anywhere is shown, never
# blocked; a find in another language than hers is only listed lower.
# Nothing personal is sent: the requests carry only the project's and its
# maker's public names.

#: Common short words per language. Small on purpose.
_STOPWORDS: dict[str, frozenset] = {
    "English": frozenset("the and of to is in it for this that with you are on be "
                         "as can your from or an by use not".split()),
    "Spanish": frozenset("el la los las de que y en un una es por para con del se "
                         "su al como más lo este puede".split()),
    "French": frozenset("le la les de des et en un une est pour que dans du sur "
                        "avec pas il vous ce qui au".split()),
    "German": frozenset("der die das und ist nicht mit den ein eine zu für von "
                        "auf sie es im dem auch wird werden".split()),
    "Portuguese": frozenset("o os a as de que e em um uma é para com não do da dos "
                            "das por se você".split()),
    "Italian": frozenset("il lo la gli le di che e è un una per con non del della "
                         "in sono questo come".split()),
}
#: Script ranges: (language, pattern, words per match). A CJK character
#: counts as half a word.
_SCRIPTS: tuple = (
    ("Japanese", re.compile(r"[぀-ヿ]"), 0.5),
    ("Korean", re.compile(r"[가-힯]"), 0.5),
    ("Chinese", re.compile(r"[一-鿿㐀-䶿]"), 0.5),
    ("Russian", re.compile(r"[Ѐ-ӿ]+"), 1.0),
    ("Arabic", re.compile(r"[؀-ۿ]+"), 1.0),
    ("Hebrew", re.compile(r"[֐-׿]+"), 1.0),
    ("Greek", re.compile(r"[Ͱ-Ͽ]+"), 1.0),
    ("Hindi", re.compile(r"[ऀ-ॿ]+"), 1.0),
    ("Thai", re.compile(r"[฀-๿]+"), 0.3),
)
_LATIN_WORD = re.compile(r"[a-zà-ÿ]+")
_CODE_BLOCK = re.compile(r"```.*?```|`[^`\n]*`", re.S)
_LINKISH = re.compile(r"https?://\S+|\]\([^)]*\)|<[^>]+>")
#: Fewer words than this says nothing about a language.
MIN_WORDS = 5


def words_language(text: Any) -> str:
    """The human language most of *text* is in ("English", "Chinese" ...), or
    "" when it is too short to tell. Script ranges first, then the share of
    common short words. No outside call, no new dependency."""
    body = _LINKISH.sub(" ", _CODE_BLOCK.sub(" ", str(text or "")))[:20000]
    latin = _LATIN_WORD.findall(body.lower())
    units: dict[str, float] = {}
    for name, pattern, weight in _SCRIPTS:
        found = pattern.findall(body)
        if found:
            units[name] = units.get(name, 0.0) + len(found) * weight
    # Japanese text also holds Chinese characters: kana says it is Japanese.
    if units.get("Japanese", 0) >= 3 and "Chinese" in units:
        units["Japanese"] += units.pop("Chinese")
    best = max(units.items(), key=lambda kv: kv[1], default=("", 0.0))
    if best[1] >= MIN_WORDS / 2 and best[1] >= len(latin) * 0.5:
        return best[0]
    if len(latin) < MIN_WORDS:
        return ""
    scores = {lang: sum(1 for w in latin if w in stops)
              for lang, stops in _STOPWORDS.items()}
    lang, hits = max(scores.items(), key=lambda kv: kv[1])
    return lang if hits >= max(2, len(latin) * 0.06) else ""


def owner_language() -> str:
    """The language the owner reads: ``owner_language`` in this bot's
    settings, else English."""
    try:
        import json
        import os
        from puffpai.config import get_config
        path = os.path.join(get_config().CONFIG_DIR, "puffpai_config.json")
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        value = raw.get("owner_language") if isinstance(raw, dict) else None
    except Exception:  # noqa: BLE001 - no settings file: the default
        value = None
    name = plain(value, 30).capitalize() if isinstance(value, str) else ""
    return name or "English"


def other_language(f: dict, mine: str = "") -> bool:
    """Are the find's words in a language other than the owner's? Unknown is
    not "other"."""
    lang = str(f.get("words_language") or "")
    return bool(lang) and lang != (mine or owner_language())


_FULL_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")
_LOGIN = re.compile(r"^[A-Za-z0-9-]{1,39}$")


def github_origin(full_name: str, *, service: str = SERVICE["github"]) -> dict:
    """Who made a GitHub project and what language its words are in:
    ``{"owner", "location", "licence", "code_language", "words_language"}``.

    Three public reads, each cached a day: the project (owner, licence, code
    language), its README, and the maker's public profile (location, only
    when they show one). Only the project's and maker's public names are sent."""
    if not _FULL_NAME.match(str(full_name or "")):
        return {}
    out = {"owner": "", "location": "", "licence": "", "code_language": "",
           "words_language": ""}
    repo = net.get(f"https://{GITHUB_API}/repos/{full_name}", hosts=[GITHUB_API],
                   service=service, accept="application/vnd.github+json", cache_s=86400)
    data = repo.json() if repo.ok else None
    if isinstance(data, dict):
        owner = data.get("owner") if isinstance(data.get("owner"), dict) else {}
        lic = data.get("license") if isinstance(data.get("license"), dict) else {}
        out.update(owner=plain(owner.get("login"), 60),
                   licence=str(lic.get("spdx_id") or "")[:60],
                   code_language=plain(data.get("language"), 30))
        out["words_language"] = words_language(data.get("description"))
    readme = net.get(f"https://{GITHUB_API}/repos/{full_name}/readme", hosts=[GITHUB_API],
                     service=service, accept="application/vnd.github.raw",
                     max_bytes=200_000, cache_s=86400)
    if readme.ok:
        lang = words_language((readme.get("body") or b"").decode("utf-8", errors="replace"))
        out["words_language"] = lang or out["words_language"]
    login = out["owner"] or full_name.split("/", 1)[0]
    if _LOGIN.match(login):
        user = net.get(f"https://{GITHUB_API}/users/{login}", hosts=[GITHUB_API],
                       service=service, accept="application/vnd.github+json",
                       cache_s=86400)
        prof = user.json() if user.ok else None
        if isinstance(prof, dict):
            out["location"] = plain(prof.get("location"), 60)
        if not out["owner"]:
            out["owner"] = login
    return out


def origin_line(origin: dict, mine: str = "") -> str:
    """"Made by X (Y). Its words are in Z." The language part only when it is
    not the owner's language; the place only when the maker shows one."""
    if not origin or not origin.get("owner"):
        return ""
    where = f" ({origin['location']})" if origin.get("location") else ""
    line = f"Made by {origin['owner']}{where}."
    lang = str(origin.get("words_language") or "")
    if lang and lang != (mine or owner_language()):
        line += f" Its words are in {lang}."
    return line


# ── the MCP Registry ────────────────────────────────────────────────────────

_STOP = frozenset("a an the and or for to of in on with my your our app apps tool tools "
                  "bot helper manager tracker planner".split())


def mcp_words(words: str) -> list[str]:
    """What the MCP Registry is asked: it matches parts of server names, so the
    words joined by "-" first, then the two longest single words."""
    ws = [w for w in re.split(r"[^a-z0-9]+", str(words or "").lower()) if w]
    asks: list[str] = []
    if len(ws) > 1:
        asks.append("-".join(ws[:3]))
    asks.extend(sorted((w for w in ws if w not in _STOP and len(w) > 2), key=len,
                       reverse=True)[:2])
    return list(dict.fromkeys(asks))[:3]


def mcp_registry(words: str, limit: int = PER_SITE) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    for ask in mcp_words(words):
        url = (f"https://{MCP_REGISTRY}/v0/servers?search={urllib.parse.quote(ask)}"
               f"&limit={int(limit)}")
        ans = net.get(url, hosts=[MCP_REGISTRY], service=SERVICE["mcp-registry"])
        data = ans.json() if ans.ok else None
        for entry in (data or {}).get("servers") or []:
            server = entry.get("server") if isinstance(entry, dict) else None
            if not isinstance(server, dict) or not server.get("name"):
                continue
            name = str(server["name"])
            meta = ((entry.get("_meta") or {}).get(
                "io.modelcontextprotocol.registry/official") or {})
            if name in seen or meta.get("isLatest") is False:
                continue          # an older version of a server already listed
            seen.add(name)
            start = mcp_start(server)
            can, why = ("add_mcp", "") if start.get("ok") else ("look", start.get("why", ""))
            repo = server.get("repository") if isinstance(server.get("repository"),
                                                          dict) else {}
            out.append(make(
                site="mcp-registry", type_="mcp", id=f"mcp:{name}",
                name=server.get("title") or name.rsplit("/", 1)[-1],
                by=name.rsplit("/", 1)[0] if "/" in name else "",
                what=server.get("description"),
                url=str(server.get("websiteUrl") or repo.get("url") or ""),
                changed=meta.get("updatedAt"), can=can, look_why=why, rank=len(out),
                raw={"server": server}))
            if len(out) >= limit:
                return out
    return out


def mcp_start(server: dict) -> dict:
    """How BuildBot would start an MCP Registry server: ``{"ok", "url" or
    "command", "headers", "why", "runs"}``. A server that needs a key or a
    setting she must type is "look at it yourself" (the tools page asks)."""
    for remote in server.get("remotes") or []:
        if not isinstance(remote, dict) or not str(remote.get("url") or "").startswith(
                "https://"):
            continue
        if remote.get("type") not in ("streamable-http", "sse", "http"):
            continue
        needs = [h for h in remote.get("headers") or []
                 if isinstance(h, dict) and (h.get("isRequired") or h.get("isSecret")
                                             or "{" in str(h.get("value") or ""))]
        if needs:
            return {"ok": False, "why": W.LOOK_WHY["mcp_secret"]}
        return {"ok": True, "url": str(remote["url"]), "headers": None,
                "runs": f"It connects to {urllib.parse.urlsplit(remote['url']).hostname}."}
    for pkg in server.get("packages") or []:
        if not isinstance(pkg, dict):
            continue
        env_needed = [e for e in pkg.get("environmentVariables") or []
                      if isinstance(e, dict) and e.get("isRequired")]
        if env_needed:
            return {"ok": False, "why": W.LOOK_WHY["mcp_secret"]}
        ident, ver = str(pkg.get("identifier") or ""), str(pkg.get("version") or "")
        transport = (pkg.get("transport") or {}).get("type") if isinstance(
            pkg.get("transport"), dict) else "stdio"
        if transport not in (None, "stdio"):
            continue
        if pkg.get("registryType") == "npm" and ident:
            cmd = f"npx -y {ident}@{ver}" if ver else f"npx -y {ident}"
        elif pkg.get("registryType") == "pypi" and ident:
            cmd = f"uvx {ident}=={ver}" if ver else f"uvx {ident}"
        else:
            continue
        return {"ok": True, "command": cmd, "headers": None,
                "runs": f"It runs this command on this computer: {cmd}"}
    return {"ok": False, "why": W.LOOK_WHY["mcp_start"]}


__all__ = ["PER_SITE", "SERVICE", "awesome_selfhosted", "clawhub", "clawhub_verify",
           "codeberg", "github", "github_origin", "make", "no_licence", "origin_line",
           "other_language", "owner_language", "words_language", "mcp_registry", "mcp_start", "mcp_words", "npm",
           "plain", "pypi", "pypi_names", "repo_type", "skills_sh"]
