"""GitHub: project search, version pins and file reads without the small "core" allowance.

Search uses only ``api.github.com/search/repositories`` (its own allowance:
10 a minute keyless, shared by the home network). Search answers already carry
stars, licence, last change, archived, fork, size, language, topics and the
default branch. Everything else comes from places that cost no API allowance:

* the version pin from ``https://github.com/<o>/<r>.git/info/refs?service=git-upload-pack``
  (first 64 KB; the default branch's commit is in the first pkt-lines). Only if
  the project answered but the branch is not there: one core call to
  ``/repos/<o>/<r>/commits/<branch>`` asking for the bare sha (at most 3 a
  build). On the older fetch path, which cannot send that Accept header, the
  same core call goes to ``/repos/<o>/<r>/git/ref/heads/<branch>`` instead (a
  small answer with the same sha). A project that refuses info/refs costs no
  core call;
* a small project (the search answer says at most :data:`ZIP_MAX_REPO_KB` KB) is
  read as ONE archive of its default branch from
  ``https://codeload.github.com/<o>/<r>/zip/refs/heads/<branch>``, pinned to
  the commit the archive itself names (GitHub writes it as the archive's
  comment; no request to github.com is needed) (at most 8 MB, read
  in memory only: nothing is unpacked to disk, nothing runs). Each file the
  reader wants is taken from it with the same 512 KB limit, and its file list
  gives the tree counts with no core call. The file host answers about once a
  second, and a project read file by file costs 5 to 12 of those turns; one
  archive costs one turn on a host of its own (file by file, 20 finds a job
  cannot be read in the search time). A larger project, or an
  archive that cannot be read, is read file by file as below;
* README, LICENSE and the mode probes from
  ``https://raw.githubusercontent.com/<o>/<r>/<pinned sha>/<path>`` (each at most
  512 KB, first hit wins, probes stop once the mode is decided). The LICENSE
  names are not asked when GitHub's own answer says the project has no licence
  file (four misses saved; a licence is then never read, which only lowers trust);
* for data-template candidates only (a find from a data search, or a find whose
  probes found nothing that runs, see ``code_mode.wants_tree``), at most 5 core
  calls a build list the tree to count data files.

Job queries carry the last 2-3 words of the phrase; data queries add ``dataset``,
then ``csv``, then ``json``, each asked only when the one before found nothing.

Search words come only from ``outbound_query.Sendable`` phrases, put into the
address only with ``outbound_query.url_quote``; the templates add only their own
qualifiers. Everything read back is data: names and descriptions are cleaned and
neutralised, and nothing here runs or imports what it reads.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import contextlib
import datetime
import html as html_lib
import io
import json
import posixpath
import re
import threading
import time
import tomllib
import urllib.parse
import zipfile
import zlib
from typing import Any, Callable, Optional

from puffpai import artifact_review
from puffpai.catalogs import code_mode
from puffpai.catalogs import searchers as S

SEARCH_HOST = "api.github.com"
RAW_HOST = "raw.githubusercontent.com"
GIT_HOST = "github.com"
CODELOAD_HOST = "codeload.github.com"

#: One search call returns up to 100 finds for the same allowance as 10
#: (measured: small fitting projects sat at places 17 to 60).
PER_PAGE = 100
MIN_STARS = 5
RECENT_DAYS = 730
SEARCH_MAX_BYTES = 1024 * 1024
REFS_MAX_BYTES = 65536
FILE_MAX_BYTES = 512 * 1024
README_KEEP_BYTES = 64 * 1024
TREE_MAX_BYTES = 4 * 1024 * 1024
MAX_SCRIPTS = 5
#: A project whose search answer says it is at most this big (KB, its whole
#: history) is first asked for as one archive. Measured on ten
#: projects: the archive of the current files was 0.4 to 1.1 times that size,
#: and 8 MB came in about a second. An archive over :data:`ZIP_MAX_BYTES` is
#: refused as it arrives (one turn on the archive host, a second or two), which
#: costs far less than reading a project file by file (5 to 12 turns on the
#: file host, which every large project shares).
ZIP_MAX_REPO_KB = 30000
#: The largest archive read for a project up to :data:`ZIP_BIG_REPO_KB` (held
#: in memory while one find is read; a larger one is refused as it arrives and
#: the project is read file by file).
ZIP_MAX_BYTES = 8 * 1024 * 1024
#: A project bigger than this (KB, search answer) may have an archive over
#: :data:`ZIP_MAX_BYTES`. Asked for with the 8 MB limit, such a project's
#: archive is refused after most of it has come (about 10 s in a recorded
#: profile) and it is then read file by file (8 to 14 turns on the file host
#: every large project shares, the measured limit on reading 20 finds a job).
#: So it is read with :data:`ZIP_BIG_MAX_BYTES`, at most :data:`ZIP_BIG_SLOTS` at a time.
ZIP_BIG_REPO_KB = 7000
#: At least 1.1 times :data:`ZIP_MAX_REPO_KB` (the archive was 0.4 to 1.1 times
#: the search answer's size), so an archive asked for is rarely refused.
ZIP_BIG_MAX_BYTES = 34 * 1024 * 1024
#: How many such archives may be held in memory at once (with the small ones,
#: at most about 12 x 8 MB + 3 x 34 MB = 200 MB during one build's reading).
ZIP_BIG_SLOTS = 3
#: Without a deadline, the longest a large archive waits for a free place.
ZIP_BIG_WAIT_S = 60.0
_BIG_SLOTS = threading.BoundedSemaphore(ZIP_BIG_SLOTS)
#: An archive listing more files than this is not read (file by file instead).
ZIP_MAX_ENTRIES = 20000
POOL_ZIP = "github_zip"
#: A project too big to read as one archive is read file by file on the file
#: host, paced 4 a second for every project at once (source_fetch's table): at most
#: this many mode probes (lockfiles included) are asked for it, and its mode
#: is "unknown" when they do not decide it (fewer finds addable, never more).
#: Seven covers a web project (package.json, three lockfiles, the page) and a
#: Python one (package.json, the page, three server files, pyproject.toml and
#: setup.py once the page folders are skipped).
PROBES_MAX = 7
MAX_LINE = 1000
MAX_TAG = 2000
MAX_QUERIES_CODE = 2
MAX_QUERIES_DATA = 1
SERVICE = "code_search"

README_NAMES = ("README.md", "readme.md", "README", "README.markdown")
LICENSE_NAMES = ("LICENSE", "LICENSE.md", "LICENSE.txt", "COPYING")
#: In an archive (or with a listing of the top, fix 2), every file at the top whose name has "licence" or "license"
#: in it or starts with "copying" (any case, any ending: LICENSE-COMMERCIAL,
#: LICENSE.commercial, LICENSE_NC.md, COPYING.LESSER, NC-LICENSE), and every
#: file right inside a top folder named LICENSES, LICENCES, LICENSE or LICENCE,
#: is read, and every one must be a licence BuildBot knows: a clean LICENSE
#: beside one that adds a condition is custom. Reading only LICENSE, COPYING
#: and UNLICENSE with .md, .txt, .rst or .markdown would miss a condition in
#: any other licence file. So "licen" followed by any of c, s, t or z, and
#: "lizen", anywhere in the name (LICENSING.md, LICENCING.txt,
#: DUAL-LICENSING.md, Lizenz.md, LICENZA, licentie.txt), not only names with
#: "licence" or "license" in them.
_LICENCE_FILE_RE = re.compile(r"^(?:.*(?:licen[cstz]|lizen[sz]).*|copying.*)$", re.I)
_LICENCE_DIR_RE = re.compile(r"^(?:licen[cstz]|lizen[sz])[a-z]*$", re.I)
#: Files at the top that may carry terms without being a licence (NOTICE,
#: COPYRIGHT, PATENTS, EULA, TERMS, LEGAL, COMMERCIAL..., any case and ending):
#: each must be a licence BuildBot knows or a notice that adds nothing
#: (:func:`where_to_look.notice_ok`).
_NOTICE_FILE_RE = re.compile(r"^(?:notice|copyright|patents?|eula|terms|legal|commercial)"
                             r"(?:[._\- ].*)?$", re.I)
#: A file is a notice file when any word of its
#: name (split at anything that is not a letter, and at capitals) is one of
#: these or starts with one of the roots below, so NONCOMMERCIAL.md,
#: ADDITIONAL_TERMS.md, RESTRICTIONS.md, PRICING.md, SPONSORWARE.md, NC.md and
#: Nutzungsbedingungen.md are read (otherwise they read as MIT). Words
#: that name ordinary project notes are not here on purpose: USAGE.md,
#: SPONSORS.md, SECURITY.md, a privacy policy (the residual: terms in a file
#: whose name says nothing about terms, or in the README).
_NOTICE_NAME_WORDS = frozenset("""
nc tos eula legal terms notice notices copyright copyrights patent patents commercial
noncommercial restriction restrictions restricted pricing price prices sponsorware addendum
additional conditions condition agreement agreements permission permissions rights acceptable
""".split())
_NOTICE_NAME_ROOTS = ("noncom", "commerc", "comerc", "kommerz", "komerc", "kommers", "restrict",
                      "nutzung", "bedingung", "condicion", "condizion", "voorwaard", "villkor",
                      "vilkar", "warunk", "podmink", "sponsorware", "addend")
#: Endings of a written document; a file named with the words above and
#: another ending (code, data) is not read as a notice file.
_NOTICE_DOC_EXT = frozenset({"md", "markdown", "txt", "text", "rst", "adoc", "asciidoc", "org",
                             "html", "htm", "pdf", "rtf", "doc", "docx", "odt"})
#: Top folders whose files are licence files, or notice files.
_NOTICE_DIR_RE = re.compile(r"^(?:legal|terms|notices?|eula|patents?|copyrights?)$", re.I)


def _name_words(file_name: str) -> list[str]:
    """"ADDITIONAL_TERMS.md" -> additional, terms, md; "SponsorWare" -> sponsor, ware, sponsorware."""
    out: list[str] = []
    for piece in re.split(r"[^A-Za-z]+", file_name):
        if not piece:
            continue
        out.append(piece.lower())
        out.extend(p.lower() for p in re.findall(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+", piece))
    return out


def _notice_name(file_name: str) -> bool:
    """Is a file with this name (no folder) a notice file? (see :data:`_NOTICE_NAME_WORDS`)"""
    if _NOTICE_FILE_RE.match(file_name):
        return True
    stem, dot, ext = file_name.rpartition(".")
    if dot and stem and ext.lower() not in _NOTICE_DOC_EXT:
        return False                          # pricing.js, permissions.py: code, not terms
    words = _name_words(file_name)
    if "use" in words and ({"policy", "fair", "acceptable", "terms"} & set(words)):
        return True                           # USE_POLICY.md, FAIR_USE.md
    return any(w in _NOTICE_NAME_WORDS or w.startswith(_NOTICE_NAME_ROOTS) for w in words)
MODE_PROBES = ("package.json", "index.html", "docs/index.html", "dist/index.html",
               "public/index.html", "Dockerfile", "docker-compose.yml", "compose.yaml",
               "pyproject.toml", "setup.py", "go.mod", "Cargo.toml")
#: Page folders not probed for a project in a language that is not a web page language.
PAGE_FOLDER_PROBES = ("docs/index.html", "dist/index.html", "public/index.html")
#: Languages whose projects are programs, services or libraries, not pages.
NOT_PAGE_LANGUAGES = frozenset({
    "Python", "Go", "Rust", "Java", "C", "C++", "C#", "PHP", "Ruby", "Scala", "Elixir",
    "Erlang", "Haskell", "Zig", "Lua", "Perl", "R", "Julia", "Shell", "PowerShell", "Nim",
    "OCaml", "Clojure", "Crystal", "Fortran", "Assembly", "Jupyter Notebook", "TeX",
    "Makefile", "Nix", "Groovy", "F#", "Visual Basic .NET", "Objective-C", "V", "D",
})

_SUBJECT_RECENT = ("https://api.github.com/search/repositories?q={words}+archived:false"
                   "+pushed:>={since}&per_page={per_page}")
_TOPIC = ("https://api.github.com/search/repositories?q=topic:{words}+stars:>={min_stars}"
          "+archived:false+fork:false&sort=stars&order=desc&per_page={per_page}")
#: Data-template queries: the job words plus one of these, tried in this order,
#: the next only when the one before found nothing.
DATA_WORDS = ("dataset", "csv", "json")
_DATA = ("https://api.github.com/search/repositories?q={words}+{data_word}+archived:false"
         "+fork:false&sort=stars&order=desc&per_page={per_page}")
_DATA_OWN = ("https://api.github.com/search/repositories?q={words}+archived:false"
             "+fork:false&sort=stars&order=desc&per_page={per_page}")
#: Data sets tagged as data sets: the job's words in projects with the GitHub
#: topic "dataset" (tier 1, when the job's subject word is not a topic).
_DATA_TOPIC = ("https://api.github.com/search/repositories?q={words}+topic:dataset"
               "+archived:false+fork:false&sort=stars&order=desc&per_page={per_page}")
#: The job's words in the name, description or README, any language (tier 2).
_DATA_README = ("https://api.github.com/search/repositories?q={words}"
                "+in:name,description,readme+archived:false+fork:false&per_page={per_page}")
#: Job queries carry at most this many of the phrase's words (the spec's "2-3 words").
JOB_WORDS_MAX = 3

# Query shapes (goal-agnostic; measured). Several
# ``language:`` qualifiers are OR-ed by GitHub, so one call asks for projects
# written for the web (the kinds Toolsmith can open inside the bot) without
# spending another search.
_WEB = "+language:JavaScript+language:HTML+language:TypeScript"
#: The phrase's words in the name or description, web languages, best match.
#: Not in the README: there a popular project that only mentions the words
#: (a "performance budget" in a browser tool, a "tracker" in an agent
#: framework) outranks every project that is about them (measured live on
#: three unrelated jobs: the README shape's top ten held 4 to 6 off-purpose
#: projects, the name and description shape's 0 to 2).
#: Open licences BuildBot can use, OR-ed like ``language:`` (measured live on
#: "chess puzzle": the job shape's top
#: 100 held 67 projects with no licence and 31 with an open one; with these
#: qualifiers, 100 with an open one). Only a job's first shape carries them:
#: the later shapes still find the server apps and the projects she can only
#: look at.
_OPEN = ("+license:mit+license:apache-2.0+license:gpl-3.0+license:bsd-3-clause"
         "+license:isc")
_JOB_WEB = ("https://api.github.com/search/repositories?q={words}+in:name,description"
            + _WEB + _OPEN + "+archived:false+fork:false&per_page={per_page}")
#: One word and a kind noun, in the name, description or topics (GitHub's default).
_JOB_WEB_NAME = ("https://api.github.com/search/repositories?q={words}" + _WEB + _OPEN
                 + "+archived:false+fork:false&per_page={per_page}")
#: Web languages, 5 stars or more, most stars first (for one broad word).
_WEB_STARS = ("https://api.github.com/search/repositories?q={words}" + _WEB
              + "+stars:>={min_stars}+archived:false+fork:false&sort=stars&order=desc"
              "&per_page={per_page}")
#: The phrase's other words, in projects tagged with its subject word
#: ("tracker" in projects tagged "budget"). ``{words}`` ends in ``+topic:<word>``.
_TOPIC_WITH = ("https://api.github.com/search/repositories?q={words}+archived:false+fork:false"
               "&sort=stars&order=desc&per_page={per_page}")
#: The phrase's words in the name, description or README, web languages. A
#: README that only mentions the words ranks a popular off-purpose project
#: high, so this shape runs last and only on spare budget, and never for one
#: bare word.
_JOB_WEB_README = ("https://api.github.com/search/repositories?q={words}"
                   "+in:name,description,readme" + _WEB
                   + "+archived:false+fork:false&per_page={per_page}")
#: Each shape has a tier. Tier 0 always runs; tier 1 only while more than
#: :data:`SECOND_SHAPE_RESERVE` searches are left in GitHub's minute; tier 2
#: only while more than :data:`THIRD_SHAPE_RESERVE` are. So later jobs still
#: get their first shape, and the extra shapes use only spare allowance.
SECOND_SHAPE_RESERVE = 5
THIRD_SHAPE_RESERVE = 6
#: Words that say "a program" without saying which: dropped from job queries
#: (a README says "app" everywhere). Never personal, never added.
FILLER_WORDS = frozenset("""
app apps application applications software program programs platform platforms system systems
website websites web online digital simple free best open source based service services
solution solutions project projects tool tools thing things
""".split())
#: Words that name the bot's role rather than what it is about. The rest of the
#: subject ("chess" in "chess coach") is its topic. Generic, never per goal.
ROLE_WORDS = frozenset("""
bot bots coach coaches tutor tutors helper helpers assistant assistants buddy buddies
companion companions guide guides mentor mentors teacher teachers trainer trainers pal pals
friend friends keeper keepers expert experts advisor advisors adviser advisers
""".split())
#: Nouns that name a kind of program. When a job's name ends in one and its
#: phrase has a single word, the noun is added (a fixed word from this list,
#: never text from the job).
KIND_NOUNS = frozenset("""
tracker planner manager organizer organiser calculator timer log logger journal checklist list
generator finder board calendar scheduler quiz flashcards flashcard dashboard editor viewer
game notebook diary reminder counter converter builder maker randomizer roller picker
inventory catalog catalogue library recorder player trainer
""".split())

OWNER_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
REPO_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
BRANCH_RE = re.compile(r"^[A-Za-z0-9._/-]{1,100}$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
TOPIC_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,49}$")
LANG_RE = re.compile(r"^[A-Za-z0-9+#. -]{1,40}$")
HOST_RE = re.compile(r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
                     r"[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]")
_URL_RE = re.compile(r"(?:https?://|www\.)\S+", re.I)

_SERVER_DEPS = code_mode.SERVER_DEPS
#: Files that hold data. YAML and XML are left out: a tree of them is far more
#: often a program's settings (Ansible playbooks, Kubernetes files, build
#: files) than a data set, so they count as "other", which weighs against data
#: (otherwise a tree of 40 playbooks reads as a data set).
_DATA_EXT = (".csv", ".tsv", ".json", ".jsonl", ".txt", ".geojson",
             ".sqlite", ".db", ".xlsx", ".xls", ".ods", ".parquet", ".bib", ".cook")
_CODE_EXT = (".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".py", ".rb", ".php", ".go", ".rs",
             ".java", ".kt", ".cs", ".c", ".cc", ".cpp", ".h", ".vue", ".svelte", ".dart",
             ".swift", ".scala", ".ex", ".exs", ".pl", ".lua", ".sh", ".ps1", ".psm1", ".bat",
             ".cmd", ".vbs", ".ahk", ".r", ".jl", ".m", ".hs", ".ml", ".clj", ".erl", ".nim",
             ".zig", ".groovy", ".kts", ".fs", ".vb", ".bash", ".zsh", ".fish", ".tcl", ".awk",
             ".html", ".htm", ".css", ".scss")
#: Pictures: a set of them is data only when they are most of the tree.
_MEDIA_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico", ".bmp", ".tif", ".tiff",
              ".avif", ".mp3", ".ogg", ".wav", ".flac")
#: Notes and licences: they decide nothing either way.
_DOC_EXT = (".md", ".markdown", ".rst", ".adoc", ".org", ".pdf")
_DOC_NAMES = ("readme", "license", "licence", "copying", "changelog", "contributing", "notice",
              "authors", "code_of_conduct", "security")
#: Files with a data ending that are a program's own settings, not data.
_NOT_DATA_NAMES = frozenset({"package.json", "package-lock.json", "tsconfig.json", "composer.json",
                             "composer.lock", ".eslintrc.json", "manifest.json", "renovate.json",
                             "requirements.txt", "pubspec.yaml", "pnpm-lock.yaml",
                             "docker-compose.yml", "docker-compose.yaml", "compose.yaml",
                             "mkdocs.yml", "_config.yml", "netlify.toml", "vercel.json",
                             "appveyor.yml", ".travis.yml", "codecov.yml", "robots.txt",
                             "cmakelists.txt"})


def _optional(modname: str):
    return S._optional(modname)


# ---------------------------------------------------------------------------
# names and addresses
# ---------------------------------------------------------------------------

def repo_ok(owner: Any, name: Any) -> bool:
    return (isinstance(owner, str) and bool(OWNER_RE.match(owner)) and isinstance(name, str)
            and bool(REPO_RE.match(name)) and name not in (".", "..")
            and not name.lower().endswith(".git"))


def branch_ok(branch: Any) -> bool:
    return (isinstance(branch, str) and bool(BRANCH_RE.match(branch)) and ".." not in branch
            and "//" not in branch and not branch.startswith(("/", "-", "."))
            and not branch.endswith(("/", ".lock", ".")))


def raw_url(owner: str, name: str, sha: str, path: str) -> str:
    if not repo_ok(owner, name) or not SHA_RE.match(sha):
        raise ValueError("not a GitHub project at a pinned commit")
    return f"https://{RAW_HOST}/{owner}/{name}/{sha}/{urllib.parse.quote(path, safe='/')}"


def refs_url(owner: str, name: str) -> str:
    if not repo_ok(owner, name):
        raise ValueError("not a GitHub project")
    return f"https://{GIT_HOST}/{owner}/{name}.git/info/refs?service=git-upload-pack"


def archive_url(found: dict[str, Any]) -> Optional[str]:
    """The download address at the pinned commit (used by Toolsmith), or None."""
    repo = found.get("repo") if isinstance(found, dict) else None
    if not isinstance(repo, dict):
        return None
    owner, name, sha = repo.get("owner"), repo.get("name"), repo.get("pinned_commit")
    if not repo_ok(owner, name) or not isinstance(sha, str) or not SHA_RE.match(sha):
        return None
    return f"https://{CODELOAD_HOST}/{owner}/{name}/tar.gz/{sha}"


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------

def _clean_text(value: Any, limit: int) -> str:
    return _CTRL_RE.sub(" ", str(value or ""))[: limit * 4].strip()


def _friendly(name: str) -> str:
    words = re.sub(r"[-_.]+", " ", name).strip()
    return (words[:1].upper() + words[1:]) if words else name


def found_from_item(item: Any, *, part: str) -> Optional[dict[str, Any]]:
    """One search answer item as ``found/v1``, or None when it is malformed."""
    if not isinstance(item, dict):
        return None
    owner = (item.get("owner") or {}).get("login") if isinstance(item.get("owner"), dict) else None
    name = item.get("name")
    if not repo_ok(owner, name) or item.get("full_name") != f"{owner}/{name}":
        return None
    if item.get("private") is True:
        return None
    description = item.get("description") if isinstance(item.get("description"), str) else ""
    if artifact_review.reads_like_orders(_URL_RE.sub(" ", description[:2000])) \
            or artifact_review.reads_like_orders(name):
        return None                      # reads like orders to a model: dropped, never shown
    branch = item.get("default_branch")
    stars = item.get("stargazers_count")
    size = item.get("size")
    lic = item.get("license") if isinstance(item.get("license"), dict) else {}
    spdx = lic.get("spdx_id") if isinstance(lic.get("spdx_id"), str) else None
    language = item.get("language") if isinstance(item.get("language"), str) \
        and LANG_RE.match(item["language"]) else None
    topics = [t for t in (item.get("topics") or []) if isinstance(t, str) and TOPIC_RE.match(t)][:20]
    pushed = item.get("pushed_at") if isinstance(item.get("pushed_at"), str) else None
    repo = {
        "host": GIT_HOST, "owner": owner, "name": name,
        "default_branch": branch if branch_ok(branch) else None,
        "pinned_commit": None, "pinned_at": None,
        "stars": stars if isinstance(stars, int) and not isinstance(stars, bool) and stars >= 0 else 0,
        "pushed_at": pushed[:10] if pushed and re.match(r"^\d{4}-\d{2}-\d{2}", pushed) else None,
        "archived": item.get("archived") is True, "fork": item.get("fork") is True,
        "license_reported": spdx[:60] if spdx else None, "license_read": None,
        "size_kb": size if isinstance(size, int) and not isinstance(size, bool) and size >= 0 else None,
        "language": language, "topics": topics, "latest_release": None,
        "in_awesome_selfhosted": False, "renamed_from": None,
    }
    url = f"https://github.com/{owner}/{name}"
    try:
        from puffpai.catalogs import trust_floor
        repo["in_awesome_selfhosted"] = bool(trust_floor.listed_lookup(url))
    except Exception:
        repo["in_awesome_selfhosted"] = False
    return S.new_found(source="github", part=part, id=f"github:{owner}/{name}", name=name,
                       friendly=_friendly(name),
                       what_it_is=_clean_text(item.get("description"), S.WHAT_IT_IS_MAX),
                       url=url, repo=repo)


def _search_url(template: str, quoted_words: str, *, since: str = "") -> str:
    return template.format(words=quoted_words, min_stars=MIN_STARS, since=since,
                           per_page=PER_PAGE)


def _run_search(url: str, *, part: str, budget, row: dict, progress) -> list[dict]:
    got = S.fetch(url, hosts=[SEARCH_HOST], max_bytes=SEARCH_MAX_BYTES, service=SERVICE,
                  pool="github_search", budget=budget, progress=progress,
                  robots=row.get("robots") != "on_request_only",
                  accept="application/vnd.github+json", cache_key=("github_search", url))
    if got.get("status") != 200 or not got.get("body"):
        return []
    try:
        data = json.loads(got["body"].decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        return []
    items = data.get("items") if isinstance(data, dict) else None
    out = []
    for item in (items if isinstance(items, list) else [])[:100]:
        rec = found_from_item(item, part=part)
        if rec is not None:
            out.append(rec)
    return out


def since_date(today: Optional[datetime.date] = None) -> str:
    day = today or datetime.datetime.now(datetime.timezone.utc).date()
    return (day - datetime.timedelta(days=RECENT_DAYS)).isoformat()


def _plain_words(text: Any) -> list[str]:
    return re.findall(r"[a-z0-9]+", str(text or "").lower())


def phrases_for_kind(job: dict, phrases: list, kind: str) -> list:
    """The Sendable phrases that belong to *job*'s *kind* part, best match first.

    A job's phrases are screened together (pictures, knowledge and code phrases
    share one source hint), so a code search must pick its own: a phrase counts
    when at least half its words are in one of the job's search phrases for
    that kind. A job that names no parts (the Add page) keeps every phrase; a
    job whose kind has no matching phrase sends nothing for it. Only the
    Sendables themselves are returned: nothing from the job text is sent.
    """
    parts = job.get("parts") if isinstance(job, dict) else None
    if not isinstance(parts, list) or not parts:
        return list(phrases)
    wanted: list[set[str]] = []
    for part in parts:
        if not isinstance(part, dict) or part.get("kind") != kind:
            continue
        for s in part.get("search") or []:
            phrase = s.get("phrase") if isinstance(s, dict) else getattr(s, "phrase", s)
            words = set(_plain_words(phrase))
            if words:
                wanted.append(words)
    if not wanted:
        return []

    def fit(p) -> float:
        words = set(str(getattr(p, "phrase", "")).split())
        if not words:
            return 0.0
        return max(len(words & w) / len(words) for w in wanted)

    ranked = sorted(((fit(p), i, p) for i, p in enumerate(phrases)), key=lambda t: (-t[0], t[1]))
    return [p for f, _i, p in ranked if f >= 0.5]


def _core_words(quoted: str) -> list[str]:
    """The phrase's words without filler ("app", "software"), last 2-3 kept.

    Works on the ``url_quote``-d phrase (split on ``+``), so nothing is re-quoted
    and no word is added. When every word is filler the phrase is kept as it is.
    """
    words = [t for t in quoted.split("+") if t]
    core = [w for w in words if w not in FILLER_WORDS]
    return (core or words)[-JOB_WORDS_MAX:]


def _topic_words(quoted: str) -> list[str]:
    """The subject without its role words: "chess+coach" gives ["chess"]."""
    words = [t for t in quoted.split("+") if t]
    core = [w for w in words if w not in ROLE_WORDS and w not in FILLER_WORDS]
    return core[-JOB_WORDS_MAX:]


def _kind_noun(job: dict, words: list[str]) -> Optional[str]:
    """A fixed kind noun from :data:`KIND_NOUNS` when the job's name ends in one."""
    name = _plain_words(job.get("name")) if isinstance(job, dict) else []
    last = name[-1] if name else ""
    if last in KIND_NOUNS and last not in words:
        return last
    return None


def _topic_query(words: list[str]) -> Optional[str]:
    """``<other words>+topic:<subject word>``: the subject word is the last word
    that is not a kind noun ("budget" in "budget tracker"); None when there is
    none, or it cannot be a GitHub topic. Only the phrase's own words (and a
    kind noun from the fixed list) are used; nothing is added."""
    subject = next((w for w in reversed(words) if w not in KIND_NOUNS), None)
    if subject is None or not re.fullmatch(r"[a-z0-9]+", subject) or not TOPIC_RE.match(subject):
        return None
    rest = [w for w in words if w != subject]
    if not rest:
        return _search_url(_TOPIC, subject)
    return _search_url(_TOPIC_WITH, "+".join(rest + ["topic:" + subject]))


def _shapes(job: dict, phrases: list, *, kind: str, row: dict,
            today: Optional[datetime.date] = None) -> list[tuple[str, str, int]]:
    """``[(url, part, tier)]``: tier 1 and 2 shapes run only while budget is
    left (see :data:`SECOND_SHAPE_RESERVE`, :data:`THIRD_SHAPE_RESERVE`)."""
    oq = _optional("puffpai.security.outbound_query")
    if oq is None:
        return []
    for p in phrases:
        if not oq.is_sendable(p):
            raise TypeError("GitHub search takes only Sendable phrases")
    template = str(row.get("query_template") or "")
    out: list[tuple[str, str, bool]] = []
    if kind == "subject":
        if job.get("id") != "subject" or not phrases:
            return []
        quoted = oq.url_quote(phrases[0])
        out.append((_search_url(_SUBJECT_RECENT, quoted, since=since_date(today)), "code-app",
                    0))
        # One word in any language, most stars first, is mostly famous projects
        # that only share the word (measured: one subject word gave a family
        # of cluster tools, and they took nine closer looks). For a
        # one-word subject it runs only on spare allowance; the web-language
        # shape below still asks for the popular ones she could open.
        one_word = len([t for t in quoted.split("+") if t]) == 1
        out.append((_search_url(template, quoted), "code-app", 1 if one_word else 0))
        topic = _topic_words(quoted)
        if len(topic) == 1:
            out.append((_search_url(_WEB_STARS, topic[0]), "code-app", 0))
        elif topic:
            out.append((_search_url(_JOB_WEB, "+".join(topic)), "code-app", 0))
        tagged = _topic_query(topic) if topic else None
        if tagged:
            out.append((tagged, "code-app", 1))
        return out
    mine = phrases_for_kind(job, phrases, kind)
    if kind == "code-app":
        for i, p in enumerate(mine[:MAX_QUERIES_CODE]):
            words = _core_words(oq.url_quote(p))
            if not words:
                continue
            noun = _kind_noun(job, words) if len(words) == 1 else None
            asked = words + [noun] if noun else words
            if noun:
                first = _search_url(_JOB_WEB_NAME, "+".join(asked))
            elif len(words) == 1:
                first = _search_url(_WEB_STARS, words[0])
            else:
                first = _search_url(_JOB_WEB, "+".join(words))
            out.append((first, "code-app", 1 if i else 0))
            # Tier 1: the subject word as a GitHub topic (any subject word, not
            # only a known list), else any language,
            # most stars. Tier 2: whatever tier 1 did not ask, then the README.
            tagged = _topic_query(asked)
            any_language = _search_url(template, "+".join(words))
            out.append((tagged or any_language, "code-app", 1))
            if tagged:
                out.append((any_language, "code-app", 2))
            if len(asked) > 1:
                out.append((_search_url(_JOB_WEB_README, "+".join(asked)), "code-app", 2))
    elif kind == "data-template":
        extra: list[tuple[str, str, int]] = []
        for p in mine[:MAX_QUERIES_DATA]:
            words = _core_words(oq.url_quote(p))
            if not words:
                continue
            said = [d for d in DATA_WORDS if d in words]
            if said:            # the phrase already names the kind of file: nothing added
                out.append((_search_url(_DATA_OWN, "+".join(words)), "data-template", 0))
            else:
                # The first data word always; the others only on spare
                # allowance (otherwise one data job can spend 3 of the build's
                # 10 searches while later jobs get none).
                for n, d in enumerate(DATA_WORDS):
                    out.append((_search_url(_DATA.replace("{data_word}", d), "+".join(words)),
                                "data-template", 0 if n == 0 else 1))
            # The same generic shapes a code job gets, on spare budget only
            # (so data jobs get a topic: and a README shape too). Tier 1: the subject word as a GitHub topic, or the words
            # in projects tagged "dataset". Tier 2: the name, description or README.
            topic = [x for x in words if x not in DATA_WORDS]
            tagged = _topic_query(topic) if topic else None
            extra.append((tagged or _search_url(_DATA_TOPIC, "+".join(topic or words)),
                          "data-template", 1))
            extra.append((_search_url(_DATA_README, "+".join(words)), "data-template", 2))
        out.extend(extra)
    return out


def _is_later_data_word(url: str) -> bool:
    """Is *url* the data search with a data word after the first ("csv", "json")?"""
    tail = _DATA.split("{data_word}", 1)[1].format(per_page=PER_PAGE)
    return any(url.endswith(f"+{d}{tail}") for d in DATA_WORDS[1:])


def would_send(job: dict, phrases: list, *, kind: str, row: dict) -> bool:
    """Would :func:`search` ask GitHub anything for these phrases? Sends nothing."""
    oq = _optional("puffpai.security.outbound_query")
    if oq is None or not all(oq.is_sendable(p) for p in phrases or []):
        return False
    return bool(_shapes(job, list(phrases or []), kind=kind, row=row))


def queries(job: dict, phrases: list, *, kind: str, row: dict,
            today: Optional[datetime.date] = None) -> list[tuple[str, str]]:
    """``[(url, part)]`` for *job*, every shape; every phrase must already be a Sendable."""
    return [(u, p) for u, p, _tier in _shapes(job, phrases, kind=kind, row=row, today=today)]


def search(job: dict, phrases: list, *, kind: str, budget, row: dict,
           progress: Optional[Callable[[str], None]] = None) -> list[dict]:
    """Registry adapter: ``found/v1`` records from GitHub search for *job*."""
    out: list[dict] = []
    seen: set[str] = set()
    data_found = False
    asked: set[str] = set()
    for url, part, tier in _shapes(job, list(phrases or []), kind=kind, row=row):
        if budget.later("github_search"):
            break
        if url in asked:
            continue
        if tier:
            left = budget.remaining("github_search")
            # Tier 2 never runs when tier 1 would not.
            reserve = (SECOND_SHAPE_RESERVE if tier == 1
                       else max(SECOND_SHAPE_RESERVE, THIRD_SHAPE_RESERVE))
            if left is not None and left <= reserve:
                continue           # keep the rest of the allowance for later jobs' first shape
        if part == "data-template" and data_found and _is_later_data_word(url):
            continue               # the next data word is asked only when this one found nothing
        asked.add(url)
        got = _run_search(url, part=part, budget=budget, row=row, progress=progress)
        data_found = data_found or (part == "data-template" and bool(got))
        for rec in got:
            if rec["id"] not in seen:
                seen.add(rec["id"])
                out.append(rec)
    return out


# ---------------------------------------------------------------------------
# the version pin
# ---------------------------------------------------------------------------

def pkt_lines(body: bytes) -> list[bytes]:
    """Complete pkt-line payloads in *body* (a truncated tail is dropped)."""
    out, i = [], 0
    while i + 4 <= len(body):
        try:
            n = int(body[i:i + 4].decode("ascii"), 16)
        except (UnicodeDecodeError, ValueError):
            break
        if n == 0:
            i += 4
            continue
        if n < 4 or i + n > len(body):
            break
        out.append(body[i + 4:i + n])
        i += n
    return out


def sha_from_refs(body: bytes, branch: str) -> Optional[str]:
    """The commit of ``refs/heads/<branch>`` in a git info/refs answer, or None."""
    if not isinstance(body, (bytes, bytearray)) or not branch_ok(branch):
        return None
    want = f"refs/heads/{branch}".encode("ascii")
    for payload in pkt_lines(bytes(body)):
        line = payload.rstrip(b"\n")
        if line.startswith(b"#"):
            continue
        sha, _, rest = line.partition(b" ")
        ref = rest.split(b"\x00", 1)[0]
        if ref == want and re.fullmatch(rb"[0-9a-f]{40}", sha):
            return sha.decode("ascii")
    return None


def head_from_refs(body: bytes) -> tuple[Optional[str], Optional[str]]:
    """``(sha, branch)`` of HEAD in a git info/refs answer (its ``symref`` names the branch)."""
    if not isinstance(body, (bytes, bytearray)):
        return None, None
    for payload in pkt_lines(bytes(body)):
        line = payload.rstrip(b"\n")
        if line.startswith(b"#"):
            continue
        sha, _, rest = line.partition(b" ")
        ref, _, caps = rest.partition(b"\x00")
        if ref != b"HEAD" or not re.fullmatch(rb"[0-9a-f]{40}", sha):
            continue
        m = re.search(rb"(?:^| )symref=HEAD:refs/heads/([^ ]{1,100})", caps)
        branch = m.group(1).decode("ascii", "replace") if m else None
        if branch is None or not branch_ok(branch):
            return None, None
        # The branch's own line must name the same commit (a HEAD that points elsewhere is not used).
        return (sha.decode("ascii"), branch) if sha_from_refs(body, branch) == sha.decode("ascii") \
            else (None, None)
    return None, None


def pin(found: dict, *, budget, progress=None) -> Optional[str]:
    """The commit of the find's default branch. When the branch is not known (a
    find from a list, not from search), the answer's HEAD names it and it is set."""
    repo = found.get("repo") or {}
    owner, name, branch = repo.get("owner"), repo.get("name"), repo.get("default_branch")
    if not repo_ok(owner, name) or (branch is not None and not branch_ok(branch)):
        return None
    got = S.fetch(refs_url(owner, name), hosts=[GIT_HOST], max_bytes=REFS_MAX_BYTES,
                  service=SERVICE, pool="github_git", budget=budget, progress=progress,
                  robots=False, allow_truncated=True, cache_key=("github_refs", owner, name))
    if got.get("status") != 200:
        # The project did not answer (blocked, gone, or we may not ask now):
        # a core call would be refused the same way, so none is spent.
        return None
    if branch is None:
        sha, head_branch = head_from_refs(got.get("body") or b"")
        if sha and head_branch:
            repo["default_branch"] = head_branch
        return sha
    sha = sha_from_refs(got.get("body") or b"", branch)
    if sha:
        return sha
    # The branch is not in the first 64 KB (many pull-request refs): one core call.
    if S.can_send_headers():
        url = (f"https://{SEARCH_HOST}/repos/{owner}/{name}/commits/"
               f"{urllib.parse.quote(branch, safe='')}")
        accept, key = "application/vnd.github.sha", ("github_commit", owner, name, branch)
    else:
        # The older fetch path sends no Accept header, so the commit answer would
        # be the whole commit (far over 4 KB). The branch's ref answer is small
        # without any header and carries the same sha.
        url = (f"https://{SEARCH_HOST}/repos/{owner}/{name}/git/ref/heads/"
               f"{urllib.parse.quote(branch, safe='/')}")
        accept, key = "application/vnd.github+json", ("github_ref", owner, name, branch)
    got = S.fetch(url, hosts=[SEARCH_HOST], max_bytes=4096, service=SERVICE, pool="github_core",
                  extra_pools=("github_core_pin",), budget=budget, progress=progress,
                  robots=False, accept=accept, cache_key=key)
    body = (got.get("body") or b"").strip()
    if got.get("status") != 200 or not body:
        return None
    text = body.decode("ascii", "replace")
    if SHA_RE.match(text):
        return text
    try:
        data = json.loads(text)
    except (ValueError, RecursionError):
        return None
    if not isinstance(data, dict):
        return None
    obj = data.get("object") if isinstance(data.get("object"), dict) else {}
    if "object" in data and (data.get("ref") != f"refs/heads/{branch}" or obj.get("type") != "commit"):
        return None
    sha = obj.get("sha") if "object" in data else data.get("sha")
    return sha if isinstance(sha, str) and SHA_RE.match(sha) else None


# ---------------------------------------------------------------------------
# reading files at the pin
# ---------------------------------------------------------------------------

class _Reader:
    """Reads files of one project at one pinned commit, against one budget pool.

    ``costly``: every file is one request on the file host.

    *url_for* builds the address of a path at the pin (GitHub's raw host by
    default; Codeberg passes its own). Every answer is data, never run.
    """

    costly = True

    def __init__(self, owner: str, name: str, sha: str, budget, progress, *,
                 url_for: Optional[Callable[[str], str]] = None, host: str = RAW_HOST,
                 pool: str = "github_raw", cache_prefix: str = "github_raw"):
        self.owner, self.name, self.sha = owner, name, sha
        self.budget, self.progress = budget, progress
        self.out_of_budget = False
        #: Paths that are there but were not read whole (bigger than
        #: FILE_MAX_BYTES): never read as missing.
        self.unreadable: set[str] = set()
        self.url_for = url_for or (lambda path: raw_url(owner, name, sha, path))
        self.host, self.pool, self.cache_prefix = host, pool, cache_prefix
        #: What is at the top of the project (and inside its licence and
        #: notice folders), from the code host's listing at the pin; None
        #: when there is none.
        self.listing: Optional[dict[str, dict]] = None
        #: True when there is no listing because the build's share of the
        #: code host's allowance was used up.
        self.unaffordable = False

    @property
    def listed(self) -> bool:
        """Is every file at the top known? Only then is every licence file read."""
        return self.listing is not None

    def load_listing(self, tree_url: Callable[[str], str], *, host: str, pool: str,
                     extra_pools: tuple = (), accept: Optional[str] = None,
                     cache_prefix: str = "github_top") -> bool:
        """Ask the code host what is at the top of the project at the pin (one
        request), and inside a top licence or notice folder (one more each, at
        most :data:`LISTING_DIRS_MAX`). False, and no listing, when any answer
        is missing, cut or not whole: then no licence read file by file is
        confirmed (:func:`read_licence`)."""
        top = _tree_listing(tree_url(self.sha), host=host, pool=pool, extra_pools=extra_pools,
                            budget=self.budget, progress=self.progress, accept=accept,
                            cache_key=(cache_prefix, self.owner, self.name, self.sha))
        if top is None:
            return False
        listing = dict(top)
        folders = [p for p, e in top.items() if e["type"] == "tree"
                   and (_LICENCE_DIR_RE.match(p) or _NOTICE_DIR_RE.match(p))]
        if len(folders) > LISTING_DIRS_MAX:
            return False
        for folder in folders:
            sha = top[folder].get("sha")
            if not isinstance(sha, str) or not SHA_RE.match(sha):
                return False
            inner = _tree_listing(tree_url(sha), host=host, pool=pool, extra_pools=extra_pools,
                                  budget=self.budget, progress=self.progress, accept=accept,
                                  cache_key=(cache_prefix, self.owner, self.name, sha))
            if inner is None:
                return False
            listing.update({f"{folder}/{p}": e for p, e in inner.items()})
        self.listing = listing
        return True

    def _known_absent(self, path: str) -> bool:
        """The listing says *path* is not a file there (no request is spent on it)."""
        if self.listing is None:
            return False
        entry = self.listing.get(path)
        if entry is not None:
            return entry["type"] != "blob"
        if "/" not in path:
            return True
        top = path.split("/", 1)[0]
        folder = self.listing.get(top)
        if folder is None or folder["type"] != "tree":
            return True
        # Inside a folder at the top: known only for the licence and notice folders.
        return bool(_LICENCE_DIR_RE.match(top) or _NOTICE_DIR_RE.match(top))

    def top_licence_files(self) -> list[str]:
        """:meth:`_ZipReader.top_licence_files`, from the listing."""
        found = {p for p, e in (self.listing or {}).items()
                 if e["type"] != "tree" and _top_licence_path(p)}
        order = {n: i for i, n in enumerate(LICENSE_NAMES)}
        return sorted(found, key=lambda p: (order.get(p, len(order)), p.lower(), p))

    def top_notice_files(self) -> list[str]:
        """:meth:`_ZipReader.top_notice_files`, from the listing."""
        return sorted(p for p, e in (self.listing or {}).items()
                      if e["type"] != "tree" and _top_notice_path(p))

    def get(self, path: str) -> Optional[bytes]:
        """The file at the pin, None when it is not there (or we may not ask)."""
        if self.out_of_budget:
            return None
        if self.listing is not None:
            entry = self.listing.get(path)
            if entry is None and self._known_absent(path):
                return None
            if entry is not None:
                if entry["type"] == "commit" or entry["mode"] == "120000" or (
                        isinstance(entry.get("size"), int) and entry["size"] > FILE_MAX_BYTES):
                    self.unreadable.add(path)     # a link, another project, or too big
                    return None
                if entry["type"] != "blob":
                    return None
        got = S.fetch(self.url_for(path), hosts=[self.host],
                      max_bytes=FILE_MAX_BYTES, service=SERVICE, pool=self.pool,
                      budget=self.budget, progress=self.progress, robots=False,
                      cache_key=(self.cache_prefix, self.owner, self.name, self.sha, path))
        if got.get("reason") in ("budget", "later"):
            self.out_of_budget = True
            return None
        if got.get("truncated") or "too_big" in str(got.get("reason") or "").replace(" ", "_"):
            self.unreadable.add(path)         # there, but longer than we read: not "missing"
            return None
        return got.get("body") if got.get("status") == 200 else None

    def first(self, names: tuple[str, ...]) -> tuple[Optional[str], Optional[bytes]]:
        for n in names:
            body = self.get(n)
            if body is not None:
                return n, body
            if self.out_of_budget:
                break
        return None, None


class _ZipReader:
    """Reads files of one project at one pinned commit from ONE archive in memory.

    The same interface as :class:`_Reader`. Paths in the archive are data:
    they are only looked up and counted, never used as a place on disk, and
    nothing is unpacked or run. A file is given only when it is a plain file
    (no link, not encrypted, stored or deflated) of at most
    :data:`FILE_MAX_BYTES`, read with that
    limit whatever size the archive claims; anything the archive cannot give
    reads as missing, as a missing file on the file host would.
    """

    costly = False                        # every probe is free once the archive is here
    listed = True                         # every file is known once the archive is here

    def __init__(self, archive: zipfile.ZipFile, members: dict[str, zipfile.ZipInfo],
                 owner: str, name: str, sha: str, skipped: Optional[set[str]] = None):
        self.archive, self.members = archive, members
        self.owner, self.name, self.sha = owner, name, sha
        self.out_of_budget = False
        #: Paths the archive has but does not give (a link, encrypted, another
        #: packing, too big, broken): there, never read as missing.
        self.unreadable: set[str] = set(skipped or ())

    @classmethod
    def from_bytes(cls, body: bytes, owner: str, name: str,
                   sha: Optional[str]) -> Optional["_ZipReader"]:
        """*sha* None: the commit is the one the archive itself names.

        GitHub writes the commit it archived as the archive's comment (40
        hex characters, as ``git archive`` does). An archive asked for by
        branch is pinned to that commit, so its files are exactly the ones
        read; an archive without a valid one is not used.
        """
        try:
            archive = zipfile.ZipFile(io.BytesIO(body))
            infos = archive.infolist()
            comment = bytes(archive.comment or b"")
        except (zipfile.BadZipFile, zipfile.LargeZipFile, ValueError, OSError, EOFError,
                NotImplementedError, RuntimeError, IndexError, KeyError, UnicodeDecodeError):
            return None
        if sha is None:
            named = comment.decode("ascii", "replace").strip()
            if not SHA_RE.match(named):
                return None
            sha = named
        if not infos or len(infos) > ZIP_MAX_ENTRIES:
            return None
        tops = {str(i.filename).split("/", 1)[0] for i in infos}
        if len(tops) != 1:
            return None                   # a code host's archive has one top folder
        prefix = tops.pop() + "/"
        members: dict[str, zipfile.ZipInfo] = {}
        skipped: set[str] = set()
        # A path written twice. A code
        # host's archive is made from one git tree, where a name is there
        # once, so two entries mean the archive is not what the host made;
        # the first was read and a second (with other terms) could be the one
        # unpacked. Neither is given: the path reads as "there, not read"
        # (a licence there is custom).
        seen: set[str] = set()
        twice: set[str] = set()
        for info in infos:
            path = str(info.filename)[len(prefix):]
            (twice if path in seen else seen).add(path)
        for info in infos:
            path = str(info.filename)[len(prefix):]
            parts = path.split("/")
            if not path or info.is_dir() or "\\" in path or "\x00" in path \
                    or any(p in ("", ".", "..") for p in parts):
                continue
            if path in twice:
                skipped.add(path)
                continue                  # written twice: never read (see above)
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                skipped.add(path)
                continue                  # a link: never followed, never read
            if info.flag_bits & 0x1:
                skipped.add(path)
                continue                  # encrypted
            if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                skipped.add(path)
                continue                  # code hosts use these two; the others are not read
                                          # with a size limit per step, so never read at all
            members.setdefault(path, info)
        return cls(archive, members, owner, name, sha, skipped=skipped - set(members))

    def get(self, path: str) -> Optional[bytes]:
        info = self.members.get(path)
        if info is None:
            return None
        if info.file_size > FILE_MAX_BYTES:
            self.unreadable.add(path)
            return None
        try:
            with self.archive.open(info) as fh:
                data = fh.read(FILE_MAX_BYTES + 1)
        except (zipfile.BadZipFile, zlib.error, ValueError, OSError, EOFError,
                NotImplementedError, RuntimeError, KeyError):
            self.unreadable.add(path)
            return None
        if len(data) > FILE_MAX_BYTES:
            self.unreadable.add(path)
            return None
        return data

    def top_licence_files(self) -> list[str]:
        """Every licence file at the top (:data:`_LICENCE_FILE_RE`) and right
        inside a top licences folder (:data:`_LICENCE_DIR_RE`), the usual
        names first, and those the archive has but does not give."""
        found = {p for p in set(self.members) | self.unreadable if _top_licence_path(p)}
        order = {n: i for i, n in enumerate(LICENSE_NAMES)}
        return sorted(found, key=lambda p: (order.get(p, len(order)), p.lower(), p))

    def top_notice_files(self) -> list[str]:
        """Every notice file at the top (:data:`_NOTICE_FILE_RE`) that is not a
        licence file, and those the archive has but does not give."""
        return sorted(p for p in set(self.members) | self.unreadable if _top_notice_path(p))

    def first(self, names: tuple[str, ...]) -> tuple[Optional[str], Optional[bytes]]:
        for n in names:
            body = self.get(n)
            if body is not None:
                return n, body
        return None, None

    def tree_counts(self) -> dict:
        return count_paths(self.members)


class _TreeReader:
    """Reads the licence and notice files at the top of a project already
    unpacked on disk (the build's download), with the interface of
    :class:`_ZipReader`, so the build reads its licence with exactly the
    rules the plan read it with (:func:`read_licence_tree`).

    A separate reader, a list of patterns checked in order with
    "non-?commercial" first, would read the GPL-2.0 ("noncommercial
    distribution"), GPL-3.0 ("noncommercially") and Unlicense ("commercial or
    non-commercial") texts as personal use only, and finds the plan offered
    as free would be refused after the download.

    Only the top folder and the licence and notice folders right inside it
    are listed. A link, or anything that is not a plain file, is never
    followed or read: it is a file the reader "has but does not give", so
    the licence reads as custom (fail closed), as in an archive.
    """

    costly = False
    listed = True

    def __init__(self, root: str, owner: str, name: str):
        import os
        self.root, self.owner, self.name, self.sha = root, owner, name, ""
        self.out_of_budget = False
        self.unreadable: set[str] = set()
        self.files: dict[str, str] = {}
        for rel, entry in self._listing(os):
            try:
                link = entry.is_symlink()
                plain = (not link) and entry.is_file(follow_symlinks=False)
            except OSError:
                link, plain = False, False
            if plain:
                self.files[rel] = entry.path
            elif link or not entry.is_dir(follow_symlinks=False):
                self.unreadable.add(rel)

    def _listing(self, os_mod) -> list:
        out = []
        try:
            top = list(os_mod.scandir(self.root))[:ZIP_MAX_ENTRIES]
        except OSError:
            return out
        for entry in top:
            out.append((entry.name, entry))
            try:
                folder = entry.is_dir(follow_symlinks=False) and not entry.is_symlink()
            except OSError:
                folder = False
            if folder and (_LICENCE_DIR_RE.match(entry.name) or _NOTICE_DIR_RE.match(entry.name)):
                try:
                    inner = list(os_mod.scandir(entry.path))[:ZIP_MAX_ENTRIES]
                except OSError:
                    self.unreadable.add(entry.name + "/")
                    continue
                out.extend((entry.name + "/" + sub.name, sub) for sub in inner)
        return out

    def get(self, path: str) -> Optional[bytes]:
        where = self.files.get(path)
        if where is None:
            return None
        try:
            with open(where, "rb") as fh:
                data = fh.read(FILE_MAX_BYTES + 1)
        except OSError:
            self.unreadable.add(path)
            return None
        if len(data) > FILE_MAX_BYTES:
            self.unreadable.add(path)
            return None
        return data

    def _known(self) -> set[str]:
        return set(self.files) | {p for p in self.unreadable if not p.endswith("/")}

    def top_licence_files(self) -> list[str]:
        found = {p for p in self._known() if _top_licence_path(p)}
        order = {n: i for i, n in enumerate(LICENSE_NAMES)}
        return sorted(found, key=lambda p: (order.get(p, len(order)), p.lower(), p))

    def top_notice_files(self) -> list[str]:
        return sorted(p for p in self._known() if _top_notice_path(p))

    def first(self, names: tuple[str, ...]) -> tuple[Optional[str], Optional[bytes]]:
        for n in names:
            body = self.get(n)
            if body is not None:
                return n, body
        return None, None


def read_licence_tree(root: str, owner: str = "", name: str = "") -> dict[str, Any]:
    """The licence of a project unpacked at *root*, read as the plan reads it
    (:func:`read_licence`, the same files and the same reader).

    ``{"file", "read", "check"}``: *read* is an SPDX id,
    :data:`where_to_look.CUSTOM_LICENSE`, or None when there is no licence
    file at all; *check* as ``repo.license_check``. *owner* and *name* are
    who the project is, from the find's address (never from the files)."""
    reader = _TreeReader(str(root), str(owner or ""), str(name or ""))
    repo: dict[str, Any] = {"owner": str(owner or ""), "license_reported": None}
    file_name, read = read_licence(reader, repo, str(name or ""))
    return {"file": file_name, "read": read, "check": bool(repo.get("license_check"))}


def _top_licence_path(path: str) -> bool:
    parts = path.split("/")
    if len(parts) == 1:
        return bool(_LICENCE_FILE_RE.match(parts[0]))
    return len(parts) == 2 and bool(_LICENCE_DIR_RE.match(parts[0])) and bool(parts[1])


def _top_notice_path(path: str) -> bool:
    """A notice file at the top, or right inside a top notice folder (legal/,
    terms/), that is not a licence file."""
    if _top_licence_path(path):
        return False
    parts = path.split("/")
    if len(parts) == 1:
        return _notice_name(parts[0])
    return len(parts) == 2 and bool(_NOTICE_DIR_RE.match(parts[0])) and bool(parts[1])


def _big_slot() -> bool:
    """A place for one large archive; False at the deadline (or after
    :data:`ZIP_BIG_WAIT_S` without one): then the project is read file by file."""
    waited = 0.0
    while not _BIG_SLOTS.acquire(timeout=0.25):
        waited += 0.25
        if S._past_deadline() or waited >= ZIP_BIG_WAIT_S:
            return False
    return True


def zip_reader(url: str, *, host: str, pool: str, owner: str, name: str, sha: Optional[str],
               budget, progress=None, max_bytes: Optional[int] = None) -> Optional[_ZipReader]:
    """One archive, read in memory; None (read file by file) when it cannot be.

    *sha* None: the archive was asked for by branch and is pinned to the
    commit it names (see :meth:`_ZipReader.from_bytes`). *max_bytes* is
    :data:`ZIP_MAX_BYTES`, or :data:`ZIP_BIG_MAX_BYTES` for a caller holding
    one of the :data:`ZIP_BIG_SLOTS` places.
    """
    if budget.later(pool) or budget.remaining(pool) == 0 or _zip_paused(budget, pool):
        return None
    with _quick(ZIP_TIMEOUT_S):
        limit = ZIP_MAX_BYTES if max_bytes is None else min(int(max_bytes), ZIP_BIG_MAX_BYTES)
        got = S.fetch(url, hosts=[host], max_bytes=limit,
                      service=SERVICE, pool=pool, budget=budget, progress=progress,
                      robots=False, cache=False)
    body = got.get("body")
    if got.get("status") is None and got.get("reason") == "no_answer":
        _zip_hung(budget, pool)       # no answer at all: the archive host is holding us
    if got.get("status") != 200 or not body or got.get("truncated"):
        return None
    return _ZipReader.from_bytes(body, owner, name, sha)


def big_archive(repo: dict) -> bool:
    """May this project's archive be over :data:`ZIP_MAX_BYTES`? (search answer size)"""
    size = repo.get("size_kb") if isinstance(repo, dict) else None
    return isinstance(size, int) and not isinstance(size, bool) and size > ZIP_BIG_REPO_KB


#: The longest one archive may keep a reader waiting without sending anything.
#: Measured: after about 17 archives in 20 s the
#: archive host answered nothing for 20 s, even for a 29 KB project, and every
#: GitHub look waited on it while fitting projects were never read.
ZIP_TIMEOUT_S = 8.0
#: After one archive that never answered, archives wait this long (the looks
#: read file by file meanwhile); after a second one, not again this build.
ZIP_COOLDOWN_S = 15.0
_ZIP_LOCK = threading.Lock()


def _quick(seconds: float):
    """A shorter wait for the request on this thread (``source_fetch.quick_timeout``)."""
    from puffpai import source_fetch
    quick = getattr(source_fetch, "quick_timeout", None)
    return quick(seconds) if callable(quick) else contextlib.nullcontext()


def _zip_paused(budget, pool: str) -> bool:
    with _ZIP_LOCK:
        until = (getattr(budget, "zip_paused", None) or {}).get(pool)
    return until is not None and time.monotonic() < until


def _zip_hung(budget, pool: str) -> None:
    """One archive never answered: pause archives a while; the second time, stop them."""
    with _ZIP_LOCK:
        paused = getattr(budget, "zip_paused", None)
        hung = getattr(budget, "zip_hung", None)
        try:
            if not isinstance(paused, dict):
                paused = {}
                budget.zip_paused = paused
            if not isinstance(hung, dict):
                hung = {}
                budget.zip_hung = hung
        except (AttributeError, TypeError):
            return
        hung[pool] = hung.get(pool, 0) + 1
        paused[pool] = time.monotonic() + ZIP_COOLDOWN_S
        second = hung[pool] >= 2
    if second:
        budget.mark_later(pool)        # no line: the files are still read one by one


def zip_url(owner: str, name: str, sha: str) -> str:
    if not repo_ok(owner, name) or not SHA_RE.match(sha):
        raise ValueError("not a GitHub project at a pinned commit")
    return f"https://{CODELOAD_HOST}/{owner}/{name}/zip/{sha}"


def zip_branch_url(owner: str, name: str, branch: str) -> str:
    if not repo_ok(owner, name) or not branch_ok(branch):
        raise ValueError("not a GitHub project branch")
    return (f"https://{CODELOAD_HOST}/{owner}/{name}/zip/refs/heads/"
            f"{urllib.parse.quote(branch, safe='/')}")


def small_enough(repo: dict) -> bool:
    size = repo.get("size_kb") if isinstance(repo, dict) else None
    return isinstance(size, int) and not isinstance(size, bool) and 0 <= size <= ZIP_MAX_REPO_KB


def _text(body: Optional[bytes]) -> str:
    return body.decode("utf-8", "replace") if body else ""


_MD_IMG = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_MD_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_MD_MARK = re.compile(r"[*_`~]+|<[^>]{0,200}>")
_BULLET = re.compile(r"^\s{0,6}(?:[-*+]|\d{1,3}[.)])\s+(.+)$")
_TABLE = re.compile(r"^\s*\|(.+)\|\s*$")


def _md_plain(text: str) -> str:
    text = _MD_IMG.sub("", text)
    text = _MD_LINK.sub(r"\1", text)
    text = html_lib.unescape(_MD_MARK.sub("", text))
    return " ".join(text.split())


def feature_lines(readme: str, *, limit: int = 40) -> list[str]:
    """Bullet and table lines from a README, cleaned (data for scoring, never orders)."""
    out: list[str] = []
    for raw in readme.splitlines()[:2000]:
        if len(raw) > MAX_LINE:
            continue                     # long lines are prose or markup, never a feature line
        m = _BULLET.match(raw)
        if m:
            line = _md_plain(m.group(1))
        else:
            t = _TABLE.match(raw)
            if not t or re.fullmatch(r"[\s|:-]+", t.group(1)):
                continue
            cells = [_md_plain(c) for c in t.group(1).split("|")]
            cells = [c for c in cells if c]
            if len(cells) < 2:
                continue
            line = f"{cells[0]}: {' '.join(cells[1:])}"
        if len(line.split()) < 3 or artifact_review.reads_like_orders(line):
            continue
        line = artifact_review.neutralise_label(line, limit=160)
        if line and line not in out:
            out.append(line)
        if len(out) >= limit:
            break
    return out


_H1_RE = re.compile(r"^#\s+(.+)$", re.M)
_SPLIT_RE = re.compile(r"\s+[·|–—-]\s+|:\s+")


#: A README's section headings: never a project's name (not "About The
#: Project").
_SECTION_HEADING_RE = re.compile(
    r"^(?:about(?: (?:the|this) (?:project|app|repo(?:sitory)?))?|table of contents|contents|"
    r"introduction|intro|overview|features|getting started|installation|install|description|"
    r"demo|live demo|readme|documentation|docs|usage|home|welcome|screenshots?|the project|"
    r"project description|summary|preview|built with|quick ?start|setup|license)$", re.I)


def friendly_from(html_text: str, readme: str, fallback: str) -> str:
    heading = _H1_RE.search(readme or "")
    for raw_title in (_title(html_text or ""), heading.group(1) if heading else None):
        if not raw_title:
            continue
        title = _md_plain(html_lib.unescape(raw_title[:300]))
        title = _SPLIT_RE.split(title)[0].strip(" -:")
        title = re.sub(r"^[^\w]+", "", title)
        if _SECTION_HEADING_RE.match(title.strip(" .!")):
            continue
        if 2 <= len(title) <= 60 and not artifact_review.reads_like_orders(title):
            return artifact_review.neutralise_label(title, limit=60)
    return fallback


_SRC_ATTR_RE = re.compile(r"\bsrc\s*=\s*[\"']([^\"'<>]{1,300})[\"']", re.I)
_INPUT_RE = re.compile(r"<(?:input|select|textarea)\b", re.I)
_KEEPS_RE = re.compile(r"localStorage|indexedDB|showSaveFilePicker")
_HOST_LIT_RE = re.compile(
    r"(?:\b(?:src|href)\s*=\s*[\"']|\bfetch\(\s*[\"'`])(?:https?:)?//([A-Za-z0-9.-]{1,253})", re.I)


def _title(html_text: str) -> Optional[str]:
    """The page's <title> text, found with a linear scan (the page is untrusted)."""
    low = html_text[:FILE_MAX_BYTES].lower()
    start = low.find("<title")
    if start < 0:
        return None
    open_end = low.find(">", start, start + MAX_TAG)
    if open_end < 0:
        return None
    close = low.find("</title", open_end + 1, open_end + 1 + MAX_TAG)
    return html_text[open_end + 1:close] if close >= 0 else None


def script_tags(html_text: str) -> list[tuple[Optional[str], str]]:
    """``[(src or None, inline body)]`` for each <script>, with a linear scan.

    A tag with no ``>`` within 2,000 characters, or a script with no closing
    tag, ends the scan: nothing on an untrusted page can make this quadratic.
    """
    low = html_text[:FILE_MAX_BYTES].lower()
    out: list[tuple[Optional[str], str]] = []
    pos = 0
    while len(out) < 500:
        start = low.find("<script", pos)
        if start < 0:
            break
        end = low.find(">", start, start + MAX_TAG)
        if end < 0:
            break
        tag = html_text[start:end]
        close = low.find("</script", end + 1)
        body = html_text[end + 1:close] if close >= 0 else ""
        m = _SRC_ATTR_RE.search(tag)
        out.append((m.group(1) if m else None, "" if m else body))
        if close < 0:
            break
        pos = close + 8
    return out


def local_scripts(html_text: str, entry: str) -> list[str]:
    """Repository paths of the page's own scripts (no outside addresses), at most 5."""
    base = posixpath.dirname(entry)
    out: list[str] = []
    for src, _body in script_tags(html_text):
        if src is None:
            continue
        src = src.strip().split("#")[0].split("?")[0]
        if not src or re.match(r"^[a-z][a-z0-9+.-]*:", src, re.I) or src.startswith("//"):
            continue
        path = posixpath.normpath(posixpath.join(base, src.lstrip("/")))
        if path.startswith("../") or path == ".." or path.startswith("/"):
            continue
        if path not in out:
            out.append(path)
        if len(out) >= MAX_SCRIPTS:
            break
    return out


def page_facts(html_text: str, scripts: dict[str, str]) -> dict[str, Any]:
    """What a page does with data, read from its text only (nothing is run)."""
    texts = [html_text] + list(scripts.values())
    joined = "\n".join(texts)
    keeps = bool(_KEEPS_RE.search(joined)) or ("new Blob(" in joined and "download" in joined)
    inline = sum(len(b.encode("utf-8")) for src, b in script_tags(html_text) if src is None)
    code_bytes = inline + sum(len(t.encode("utf-8")) for t in scripts.values())
    hosts: list[str] = []
    for h in _HOST_LIT_RE.findall(joined):
        h = h.lower().strip(".")
        if HOST_RE.match(h) and h not in hosts:
            hosts.append(h)
    return {"keeps_data": keeps, "inputs": len(_INPUT_RE.findall(joined)),
            "code_bytes": code_bytes, "runtime_hosts_guess": hosts[:30]}


def package_facts(body: Optional[bytes]) -> Optional[dict[str, Any]]:
    try:
        data = json.loads(_text(body))
    except (ValueError, RecursionError):
        return {"scripts": [], "bin": False, "deps": []}
    if not isinstance(data, dict):
        return {"scripts": [], "bin": False, "deps": []}
    scripts = data.get("scripts") if isinstance(data.get("scripts"), dict) else {}
    deps = data.get("dependencies") if isinstance(data.get("dependencies"), dict) else {}
    name_ok = re.compile(r"^[@a-z0-9][a-z0-9._/@-]{0,120}$", re.I)
    return {"scripts": [k for k in list(scripts)[:60] if isinstance(k, str) and name_ok.match(k)],
            "bin": bool(data.get("bin")),
            "deps": [k for k in list(deps)[:300] if isinstance(k, str) and name_ok.match(k)]}


def python_console_scripts(path: str, body: Optional[bytes]) -> bool:
    text = _text(body)
    if path == "pyproject.toml":
        try:
            data = tomllib.loads(text)
        except (tomllib.TOMLDecodeError, ValueError, RecursionError):
            return False
        project = data.get("project") if isinstance(data.get("project"), dict) else {}
        poetry = ((data.get("tool") or {}).get("poetry") or {}) if isinstance(data.get("tool"), dict) else {}
        return bool(project.get("scripts") or (isinstance(poetry, dict) and poetry.get("scripts")))
    return "console_scripts" in text


def _tree_counts(owner: str, name: str, sha: str, *, budget, progress,
                 code_find: bool = False) -> Optional[dict]:
    url = f"https://{SEARCH_HOST}/repos/{owner}/{name}/git/trees/{sha}?recursive=1"
    # Code finds may use only part of the tree allowance, so a data search later
    # in the same build still has listings left (github_core_tree_code cap).
    pools = ("github_core_tree", "github_core_tree_code") if code_find else ("github_core_tree",)
    got = S.fetch(url, hosts=[SEARCH_HOST], max_bytes=TREE_MAX_BYTES, service=SERVICE,
                  pool="github_core", extra_pools=pools, budget=budget,
                  progress=progress, robots=False, accept="application/vnd.github+json",
                  cache_key=("github_tree", owner, name, sha))
    if got.get("status") != 200 or not got.get("body"):
        return None
    try:
        tree = json.loads(got["body"].decode("utf-8")).get("tree")
    except (UnicodeDecodeError, ValueError, AttributeError, RecursionError):
        return None
    return count_paths(str(node.get("path") or "") for node in
                       (tree if isinstance(tree, list) else [])
                       if isinstance(node, dict) and node.get("type") == "blob")


#: The largest listing of one folder read (a top folder of about 7,000 entries).
LISTING_MAX_BYTES = 1024 * 1024
#: At most this many top licence or notice folders are listed; more: no listing.
LISTING_DIRS_MAX = 2
#: Core calls a build keeps back from top listings (for data finds' tree counts).
LISTING_CORE_RESERVE = 2
_LISTING_TYPES = frozenset({"blob", "tree", "commit"})


def _tree_listing(url: str, *, host: str, pool: str, budget, progress, extra_pools: tuple = (),
                  accept: Optional[str] = None,
                  cache_key: Optional[tuple] = None) -> Optional[dict[str, dict]]:
    """One folder's entries from a code host's tree answer (GitHub's and
    Gitea's have the same shape): ``{name: {"type","mode","size","sha"}}``.

    None, never a part, when the answer is missing, cut, says it is
    truncated, is one page of several, or holds an entry that cannot be read
    (a listing that is not whole cannot say every licence file was read). The answer is data: names are only looked
    up, never used as a place on disk.
    """
    got = S.fetch(url, hosts=[host], max_bytes=LISTING_MAX_BYTES, service=SERVICE, pool=pool,
                  extra_pools=extra_pools, budget=budget, progress=progress, robots=False,
                  accept=accept, cache_key=cache_key)
    if got.get("status") != 200 or not got.get("body") or got.get("truncated"):
        return None
    try:
        data = json.loads(got["body"].decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        return None
    if not isinstance(data, dict) or data.get("truncated") is not False:
        return None
    tree = data.get("tree")
    if not isinstance(tree, list):
        return None
    total = data.get("total_count")
    if total is not None and (not isinstance(total, int) or isinstance(total, bool)
                              or total != len(tree)):
        return None                           # one page of a longer listing (Gitea)
    out: dict[str, dict] = {}
    for node in tree:
        if not isinstance(node, dict):
            return None
        path, kind, mode = node.get("path"), node.get("type"), node.get("mode")
        size = node.get("size")
        if not isinstance(path, str) or not path or "/" in path or "\x00" in path \
                or path in (".", "..") or kind not in _LISTING_TYPES or not isinstance(mode, str):
            return None
        if size is not None and (not isinstance(size, int) or isinstance(size, bool)):
            return None
        out[path] = {"type": kind, "mode": mode, "size": size, "sha": node.get("sha")}
    return out


def tree_top_url(owner: str, name: str) -> Callable[[str], str]:
    """The address of one folder's listing on GitHub, by its tree (or commit) sha."""
    return lambda sha: f"https://{SEARCH_HOST}/repos/{owner}/{name}/git/trees/{sha}"


def count_paths(paths) -> dict:
    """``{"data","code","files","media","other"}`` counts for a project's file paths."""
    data = code = files = media = other = 0
    for raw_path in paths:
        path = str(raw_path).lower()
        base = posixpath.basename(path)
        files += 1
        if base in _NOT_DATA_NAMES:
            continue                     # a program's own settings decide nothing
        if any(part.startswith(".") for part in path.split("/")):
            # A file in a dot folder (.github, .vscode, .devcontainer) is the
            # project's tooling, never its data (30 JSON files under .github/
            # do not make a project a data set).
            continue
        if path.endswith(_DATA_EXT):
            data += 1
        elif path.endswith(_CODE_EXT):
            code += 1
        elif path.endswith(_MEDIA_EXT):
            media += 1
        elif path.endswith(_DOC_EXT) or base.startswith(_DOC_NAMES) or base.startswith("."):
            continue                     # notes, licences and dot files decide nothing
        else:
            other += 1                   # an unknown kind (a script in a language not listed)
    return {"data": data, "code": code, "files": files, "media": media, "other": other}


def _decided(d: dict[str, Any], probed: set[str]) -> bool:
    """Can more probes still change the mode? False means keep probing."""
    markers = set(d["root_markers"])
    pj = d.get("package_json")
    entry = d.get("entry_html")
    if pj is not None:
        if set(pj.get("deps") or []) & _SERVER_DEPS:
            return True
        if d.get("has_build_script") and (markers & set(code_mode.LOCKFILES) or entry):
            return True
        if pj.get("bin") and not d.get("has_build_script"):
            return True
    if entry and entry != "docs/index.html" and not d.get("has_build_script"):
        return True
    if markers & {"Dockerfile", "docker-compose.yml", "compose.yaml"}:
        return True          # every page probe came earlier in the order
    if markers & {"pyproject.toml", "setup.py", "go.mod", "Cargo.toml"}:
        return True
    return False


def read_details(found: dict[str, Any], *, budget, progress=None) -> dict[str, Any]:
    """Fill the pin, the licence read and ``details`` for a GitHub find (in place)."""
    repo = found.get("repo") or {}
    owner, name = repo.get("owner"), repo.get("name")
    if not repo_ok(owner, name):
        return found
    # A small project: its default branch as one archive, pinned to the commit
    # the archive names. One request on the archive host and none on
    # github.com, so projects read this way and projects read file by file
    # (pin on github.com, files on the file host) never wait for each other.
    branch = repo.get("default_branch")
    if small_enough(repo) and branch_ok(branch):
        # A large archive holds one of ZIP_BIG_SLOTS places from before it is
        # asked for until the find has been read from it, so at most that many
        # are in memory at once; none free by the deadline: file by file.
        big = big_archive(repo)
        if not big or _big_slot():
            try:
                archive = zip_reader(zip_branch_url(owner, name, branch), host=CODELOAD_HOST,
                                     pool=POOL_ZIP, owner=owner, name=name, sha=None,
                                     budget=budget, progress=progress,
                                     max_bytes=ZIP_BIG_MAX_BYTES if big else ZIP_MAX_BYTES)
                if archive is not None:
                    return read_at_pin(found, archive,
                                       tree_counter=lambda _code_find: archive.tree_counts(),
                                       account_name=_account_name_lookup(owner, budget, progress))
            finally:
                if big:
                    _BIG_SLOTS.release()
    sha = pin(found, budget=budget, progress=progress)
    if not sha:
        return found
    reader = _Reader(owner, name, sha, budget, progress)
    left = budget.remaining("github_core")
    if repo.get("license_reported") is not None or "license_reported" not in repo:
        if left is None or left > LISTING_CORE_RESERVE:
            # File by file there is no archive to list: one listing of the
            # top (a core call, cached a day) tells which licence and notice
            # files are there, and which probes would miss. Without it no
            # licence read this way is confirmed. A project GitHub says has no licence file is not listed:
            # nothing to confirm. The last LISTING_CORE_RESERVE core calls
            # are left for the tree counts of data finds.
            reader.load_listing(tree_top_url(owner, name), host=SEARCH_HOST, pool="github_core",
                                accept="application/vnd.github+json")
        if not reader.listed and _core_spent(budget):
            # No listing because this build's share of GitHub's
            # keyless allowance is used up, not because of anything in the
            # project. Its licence is not confirmed (still never usable), and
            # the plan says why in plain words (where_to_look.UNCHECKED_LICENSE).
            reader.unaffordable = True

    def tree_counter(code_find: bool) -> Optional[dict]:
        if code_find and not small_enough(repo):
            # Reading limit: a code find too big for one archive
            # is not counted file by file. Its whole tree is one core call of
            # up to 4 MB, often cut (so no count at all), and that call is one
            # fewer listing for a find whose licence it would confirm. Without
            # a count it is never a data set (fewer finds addable, never more).
            return None
        return _tree_counts(owner, name, sha, budget=budget, progress=progress,
                            code_find=code_find)

    return read_at_pin(found, reader, tree_counter=tree_counter,
                       probe_cap=None if small_enough(repo) else PROBES_MAX,
                       account_name=_account_name_lookup(owner, budget, progress))


def _core_spent(budget) -> bool:
    """Is this build's share of GitHub's core allowance used up (or GitHub
    asked for a long wait), so a listing could not be had?"""
    try:
        left = budget.remaining("github_core")
        return budget.later("github_core") or (left is not None and left <= LISTING_CORE_RESERVE)
    except Exception:  # noqa: BLE001 - a budget that cannot say: not known to be spent
        return False


#: Core calls a build keeps before asking for an account's public name
#: (listings of finds read file by file come first).
NAME_CORE_RESERVE = LISTING_CORE_RESERVE + 2
ACCOUNT_MAX_BYTES = 16 * 1024


def _account_name_lookup(owner: str, budget, progress) -> Callable[[], Optional[str]]:
    """A callable giving the owner account's public name (or None), asked
    only when a licence's holder is a stranger to the account's login.

    One core call to ``/users/<login>`` (cached a day), capped per build by
    the ``github_core_name`` pool and only while more than
    :data:`NAME_CORE_RESERVE` core calls are left. The answer is data: only
    its "name" is read, and only a plain name is used
    (:meth:`where_to_look.OwnerName.from_host`)."""

    def ask() -> Optional[str]:
        try:
            left = budget.remaining("github_core")
            if not OWNER_RE.match(str(owner or "")) or budget.later("github_core") \
                    or (left is not None and left <= NAME_CORE_RESERVE):
                return None
            got = S.fetch(f"https://{SEARCH_HOST}/users/{owner}", hosts=[SEARCH_HOST],
                          max_bytes=ACCOUNT_MAX_BYTES, service=SERVICE, pool="github_core",
                          extra_pools=("github_core_name",), budget=budget, progress=progress,
                          robots=False, accept="application/vnd.github+json",
                          cache_key=("github_user", owner.lower()))
            if got.get("status") != 200 or not got.get("body") or got.get("truncated"):
                return None
            data = json.loads(got["body"].decode("utf-8"))
        except Exception:  # noqa: BLE001 - no name: the licence stays marked to check
            return None
        if not isinstance(data, dict) or str(data.get("login") or "").lower() != owner.lower():
            return None
        from puffpai.catalogs import where_to_look
        return where_to_look.OwnerName.from_host(data.get("name"))

    def lookup() -> Optional[str]:
        """Each account asked at most once a build, whatever it answered."""
        key = str(owner or "").lower()
        with _NAMES_LOCK:
            asked = getattr(budget, "account_names", None)
            if not isinstance(asked, dict):
                asked = {}
                try:
                    budget.account_names = asked
                except (AttributeError, TypeError):
                    return None
            if key in asked:
                return asked[key]
            asked[key] = None             # taken: another reader of this account waits for none
        name = ask()
        with _NAMES_LOCK:
            asked[key] = name
        return name

    return lookup


_NAMES_LOCK = threading.Lock()


def read_licence(reader: Any, repo: dict[str, Any], name: str, *,
                 no_licence_file: bool = False) -> tuple[Optional[str], Optional[str]]:
    """``(licence file name, licence read)`` at the reader's pin.

    The read is an SPDX id, :data:`where_to_look.CUSTOM_LICENSE`, or None
    (nothing read: the licence the host reports is all there is). Read with
    who the project is (its owner and name from the find's address), so a
    copyright line can name the project itself.

    Fail closed, never "missing" when a licence is there but not read:

    * a file that was read and is not a licence BuildBot knows is custom
      (so GitHub's reported MIT never stands in for an MIT file whose
      copyright line adds a condition);
    * a licence file that is there but was not read whole (bigger than
      :data:`FILE_MAX_BYTES`, a link, encrypted) is custom: before, it read
      as missing, so a condition past the cut, or the reported licence,
      stood in;
    * when every file at the top is known (an archive, or the code host's
      listing at the pin), every licence file at the top is read and every
      one must be a licence BuildBot knows, and every notice file must be
      one too or add nothing; a reported licence with no licence file there
      is custom;
    * read file by file with no listing (fix 2), no licence is confirmed:
      custom when the host reports one or a usual licence file is there.
    """
    from puffpai.catalogs import where_to_look
    custom = where_to_look.CUSTOM_LICENSE
    ident: tuple = (str(repo.get("owner") or ""), str(name))
    costly = getattr(reader, "costly", True)
    if costly and no_licence_file:
        return None, None

    texts: list[str] = []                     # every licence and notice file read

    def reading(body: bytes) -> str:
        texts.append(_text(body))
        return where_to_look.license_from_text(texts[-1], identity=ident) or custom

    def together(name_read: tuple) -> tuple:
        """Every holder the files name, read together (fix 1): a real name in
        LICENSE and a phrase as the only holder of LICENSE.md is custom."""
        if name_read[1] in (None, custom) or len(texts) < 2:
            return name_read
        holders = [h for t in texts for h in where_to_look.licence_holders(t, identity=ident)]
        if not where_to_look.holders_ok_together(holders, identity=ident):
            return name_read[0], custom
        return name_read

    listed = getattr(reader, "listed", None)
    if listed is None:
        listed = not costly
    if not listed or not callable(getattr(reader, "top_licence_files", None)):
        # Read file by file without a listing, BuildBot cannot know which other licence or
        # notice files are there (LICENSE-COMMERCIAL, LICENSING.md, NOTICE,
        # ...), so no licence read this way is confirmed: custom when there
        # is one, and none of the file host's turns are spent on it. (Asking
        # only the four usual names would leave the other names unread; the
        # listing closes that.)
        # When the listing could not be had only because the
        # build's share of the keyless allowance was used up, the licence is
        # "not checked" (still never usable), so the plan does not claim it
        # has terms of its own.
        unconfirmed = (where_to_look.UNCHECKED_LICENSE if getattr(reader, "unaffordable", False)
                       else custom)
        if repo.get("license_reported"):
            return None, unconfirmed
        lic_name, _lic = reader.first(LICENSE_NAMES)
        unreadable = set(getattr(reader, "unreadable", ()) or ())
        if lic_name:
            return lic_name, unconfirmed
        if unreadable & set(LICENSE_NAMES):
            return sorted(unreadable & set(LICENSE_NAMES))[0], custom
        return None, None
    # Every file at the top is known (an archive, or a listing at the pin):
    # every licence file is read, and every one must be a licence BuildBot
    # knows; every notice file must be one too or add nothing.
    names = reader.top_licence_files()
    if not names:
        return (None, custom) if repo.get("license_reported") else (None, None)
    first = next((n for n in LICENSE_NAMES if n in names), names[0])
    notices = reader.top_notice_files() if callable(
        getattr(reader, "top_notice_files", None)) else []
    fetched: dict[str, Any] = {}

    def get(n: str) -> Any:
        """Each file asked once: the second read below asks nothing again."""
        if n not in fetched:
            fetched[n] = reader.get(n)
        return fetched[n]

    def judge() -> tuple:
        texts.clear()
        reads = []
        for n in names:
            body = get(n)
            reads.append(custom if body is None else reading(body))
        if any(r == custom for r in reads):
            return first, custom
        main = reads[names.index(first)]
        for n in notices:
            body = get(n)
            if body is None:
                return first, custom          # there, but not given whole (or not asked)
            text = _text(body)
            texts.append(text)
            if where_to_look.license_from_text(text, identity=ident):
                continue                      # a licence BuildBot knows (e.g. COPYRIGHT holding MIT)
            if not where_to_look.notice_ok(text, identity=ident, apache=(
                    main == "Apache-2.0" and n.lower().startswith("notice"))):
                return first, custom
        return together((first, main))

    got = judge()
    # Read again with only the owner's
    # account trusted as a holder (where_to_look.strict_names). A licence
    # that reads only the first way names a holder BuildBot cannot tell from
    # a restriction in a language no list has: it is read and shown, and
    # marked so it is never ticked by itself (trust_floor.CHECK_LICENCE).
    if got[1] not in (None, custom):
        with where_to_look.strict_names():
            strict = judge()
        repo["license_check"] = strict[1] != got[1]
        # Say what to check, in one short line (a bare "check this licence
        # yourself" says nothing about what or why).
        bodies = [fetched[n] for n in list(names) + list(notices) if fetched.get(n) is not None]
        repo["license_check_why"] = _check_why(bodies, ident) if repo["license_check"] else ""
        if repo["license_check_why"] == "stranger":
            base = ident

            def recheck(owner_name: str) -> None:
                """So a real holder line is not read as "it names someone
                other than the author": read again strictly with the
                account's public name as the owner's (a person's own name on
                an account named otherwise), from the texts already read.
                Only a holder that IS that name, word for word, becomes sure;
                a condition word still marks it."""
                nonlocal ident
                ident = base + (where_to_look.OwnerName(owner_name),)
                try:
                    with where_to_look.strict_names():
                        again = judge()
                    if again[1] == got[1]:
                        repo["license_check"], repo["license_check_why"] = False, ""
                    else:
                        repo["license_check_why"] = _check_why(bodies, ident)
                finally:
                    ident = base

            try:
                reader.holder_recheck = recheck
            except (AttributeError, TypeError):
                pass
    return got


def _check_why(bodies: list, ident: tuple) -> str:
    """The reason a licence read is marked to check (where_to_look.CHECK_WHY):
    the most telling one any of the project's licence or notice files gives,
    "unsure" when none says."""
    from puffpai.catalogs import where_to_look
    whys = {where_to_look.licence_holders_why(_text(b), identity=ident) for b in bodies}
    return next((w for w in where_to_look.CHECK_WHY if w in whys), "unsure")


def read_at_pin(found: dict[str, Any], reader: "_Reader", *,
                tree_counter: Optional[Callable[[bool], Optional[dict]]] = None,
                probe_cap: Optional[int] = None,
                account_name: Optional[Callable[[], Optional[str]]] = None) -> dict[str, Any]:
    """Fill the pin, the licence read, ``details`` and the mode from *reader*'s files (in place).

    Shared by every code host that pins a commit (GitHub, Codeberg): the same
    files in the same order, the same licence reader and the same mode rules.
    *tree_counter* lists the tree for data-template candidates (None: no listing).
    *probe_cap*: at most that many mode probes are asked (see :data:`PROBES_MAX`).
    """
    repo = found.get("repo") or {}
    name = repo.get("name") or ""
    repo["pinned_commit"] = reader.sha
    repo["pinned_at"] = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    d = S.empty_details()
    readme_name, readme = reader.first(README_NAMES)
    readme_text = _text(readme)[:README_KEEP_BYTES]
    if readme_name:
        d["root_markers"].append(readme_name)
    d["readme_text"] = readme_text
    d["feature_lines"] = feature_lines(readme_text)
    # GitHub's own answer says whether the project has a licence file at all
    # ("license": null). Then the four names are not asked one by one on the
    # file host: nothing would be found, and not reading can only lower trust.
    no_licence_file = (repo.get("host") == GIT_HOST and "license_reported" in repo
                       and repo.get("license_reported") is None)
    lic_name, read = read_licence(reader, repo, str(name), no_licence_file=no_licence_file)
    if lic_name:
        d["root_markers"].append(lic_name)
    if read:
        repo["license_read"] = read

    entry_text = ""
    probed: set[str] = set()
    console_scripts = False           # read here, kept out of the found/v1 details
    # Every file read waits its turn at the file host (about one a second), and
    # most probes miss. A project written in a language that is not a web page
    # language does not keep its page in docs/, dist/ or public/ often enough
    # to spend three reads on: those three are skipped (the root index.html is
    # still read). Skipping only ever gives fewer facts, never more trust.
    language = str(repo.get("language") or "")
    # A project read file by file because it is too big for one archive keeps
    # its page in a folder rarely enough (a large project builds its page) to
    # skip the three folder reads too.
    skip = PAGE_FOLDER_PROBES if (language in code_mode.APP_LANGUAGES
                                  or language in NOT_PAGE_LANGUAGES
                                  or probe_cap is not None) else ()
    # Probing stopped before the mode was decided (the budget, the deadline, or
    # PROBES_MAX asked on the file host): the facts so far could name a mode
    # the rest would have ruled out, so the mode is "unknown", never a guess
    # from half the facts.
    cut = False
    asked = 0
    if not code_mode._is_mcp(found):
        for path in MODE_PROBES:
            if _decided(d, probed):
                break
            if reader.out_of_budget or (probe_cap is not None and asked >= probe_cap):
                cut = True
                break
            if path.endswith("index.html") and d["entry_html"]:
                continue
            if path in skip:
                continue
            # A probe the listing of the top already answers costs no turn on
            # the file host, so it does not count.
            known_absent = getattr(reader, "_known_absent", None)
            free = callable(known_absent) and known_absent(path)
            body = reader.get(path)
            if not free:
                asked += 1
            probed.add(path)
            if body is None:
                continue
            if path in code_mode.ENTRY_HTML:
                d["entry_html"] = path
                entry_text = _text(body)
                if path == "index.html":
                    d["root_markers"].append(path)
                continue
            d["root_markers"].append(path)
            if path == "package.json":
                d["package_json"] = package_facts(body)
                d["has_build_script"] = "build" in d["package_json"]["scripts"]
                if d["has_build_script"]:
                    lock_name, _ = reader.first(code_mode.LOCKFILES)
                    asked += 1 + (code_mode.LOCKFILES.index(lock_name) if lock_name
                                  else len(code_mode.LOCKFILES) - 1)
                    if lock_name:
                        d["root_markers"].append(lock_name)
            elif path in ("pyproject.toml", "setup.py"):
                console_scripts = console_scripts or python_console_scripts(path, body)

    if d["entry_html"] and entry_text and not d["has_build_script"]:
        scripts: dict[str, str] = {}
        for sp in local_scripts(entry_text, d["entry_html"]):
            body = reader.get(sp)
            if body is not None:
                scripts[sp] = _text(body)
        d.update(page_facts(entry_text, scripts))
    friendly = friendly_from(entry_text, readme_text, found.get("friendly") or _friendly(name))
    found["friendly"] = friendly

    counts = None
    found["details"] = d
    if tree_counter is not None and code_mode.wants_tree(found):   # data-template candidates only
        counts = tree_counter(found.get("part") != "data-template")
    # The mode is decided here, with the two facts that have no place in the
    # found/v1 details (console scripts, tree counts); details keep only the
    # pinned keys.
    facts = dict(d, console_scripts=console_scripts)
    if counts is not None:
        facts["tree"] = counts
    found["details"] = facts
    undecided = (cut or reader.out_of_budget) and not _decided(d, probed)
    found["mode_guess"] = "unknown" if undecided else code_mode.guess(found)
    found["details"] = d
    # A licence marked to check only because its holder is not
    # the account's login is read again with the account's public name, but
    # only for a find BuildBot could add (the name costs one core call, which
    # a listing would otherwise use; at most once per account and build).
    recheck = getattr(reader, "holder_recheck", None)
    if callable(recheck) and callable(account_name) \
            and found["mode_guess"] in code_mode.INTEGRABLE_MODES:
        owner_name = account_name()
        if owner_name:
            recheck(owner_name)
    return found


def details(found: dict[str, Any], *, budget, row: Optional[dict] = None,
            progress: Optional[Callable[[str], None]] = None) -> dict[str, Any]:
    """Registry adapter for the ``github_details`` row."""
    return read_details(found, budget=budget, progress=progress)


__all__ = ["LICENSE_NAMES", "MODE_PROBES", "README_NAMES", "archive_url", "branch_ok",
           "count_paths", "details", "feature_lines", "small_enough", "zip_branch_url",
           "zip_reader", "zip_url", "found_from_item", "friendly_from", "local_scripts",
           "package_facts", "page_facts", "phrases_for_kind", "pin", "pkt_lines", "queries",
           "raw_url", "read_at_pin", "read_details", "read_licence_tree", "refs_url",
           "repo_ok", "search",
           "sha_from_refs", "since_date", "would_send"]
