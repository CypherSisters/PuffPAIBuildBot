"""What kind of thing a found project is, from facts read without running it.

:func:`guess` reads a ``found/v1`` record (see ``where_to_look``) and says how
BuildBot could use it: a page that opens as it is (``static_web``), a page that
must be built first (``web_build``), a command-line tool, a whole server
program, a library, a data or template set, a connector for an AI
(``mcp_server``), a look, or ``unknown``. Nothing here fetches, imports or runs
anything: it only reads the file names, ``package.json`` fields and page facts
that :func:`puffpai.catalogs.where_to_look.details` already gathered.

The rules know no goal and no project. They read markers every project of a
kind carries (a build script, a lock file, a Dockerfile, a console script),
in a fixed order, so the same project always gets the same answer.

:func:`candidate_text` is the one text shape used both for the vectors shipped
beside every snapshot and for online finds at run time (``candidate_v1``).

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import re
from typing import Any

from puffpai import artifact_review

CANDIDATE_TEXT_SHAPE = "candidate_v1"
CANDIDATE_TEXT_MAX = 300

MODES = ("static_web", "web_build", "server_app", "node_lib", "python_lib", "cli_tool",
         "data_template", "mcp_server", "theme", "other", "unknown")

#: Modes BuildBot can add to a bot (pictures are integrable by part, not mode).
INTEGRABLE_MODES = frozenset({"static_web", "web_build", "cli_tool", "data_template"})

#: Where a page that opens as it is usually lives.
ENTRY_HTML = ("index.html", "docs/index.html", "dist/index.html", "public/index.html")
LOCKFILES = ("package-lock.json", "pnpm-lock.yaml", "yarn.lock")
#: Root files that only a program meant to run as a server carries.
SERVER_FILES = frozenset({"Dockerfile", "docker-compose.yml", "docker-compose.yaml",
                          "compose.yml", "compose.yaml", "manage.py", "composer.json",
                          "Gemfile", "config.ru", "Procfile", "artisan", "server.js",
                          "pnpm-workspace.yaml", "lerna.json"})
_SERVER_SUFFIXES = (".sln", ".csproj")
_CONTAINER_FILES = frozenset({"Dockerfile", "docker-compose.yml", "docker-compose.yaml",
                              "compose.yml", "compose.yaml"})
#: Languages whose projects, when not a page, are almost always server programs.
SERVER_LANGUAGES = frozenset({"PHP", "Ruby", "C#", "Java", "Blade", "Elixir"})
#: Languages of phone and desktop apps: nothing BuildBot can add.
APP_LANGUAGES = frozenset({"Dart", "Swift", "Kotlin", "Objective-C"})

#: A dependency on one of these means the project is a server program.
SERVER_DEPS = frozenset({"express", "fastify", "koa", "hapi", "@hapi/hapi", "nuxt",
                         "@nestjs/core", "next"})

#: Languages of programs (a project written in one is code, whatever it says it holds).
PROGRAM_LANGUAGES = SERVER_LANGUAGES | APP_LANGUAGES | frozenset({
    "Go", "Rust", "Python", "JavaScript", "TypeScript", "C", "C++"})
#: Manifests that mean "this is a program", read by the probes.
_PROGRAM_MARKERS = frozenset({"package.json", "pyproject.toml", "setup.py", "go.mod", "Cargo.toml"})
_DATA_NAME_RE = re.compile(r"(?:^|[^a-z])(?:data ?sets?|databases?|data)(?:$|[^a-z])", re.I)

_TEMPLATE_RE = re.compile(r"(?:^|[^a-z])(?:template|theme)s?(?:$|[^a-z])", re.I)
_MCP_RE = re.compile(r"(?:^|[^a-z0-9])mcp(?:$|[^a-z0-9])|model context protocol", re.I)
_SERVER_README_RE = re.compile(
    r"self[- ]?host|docker|web (?:server|interface|ui)|http server|listens on|"
    r"localhost:\d+|port \d+", re.I)


def _details(found: dict[str, Any]) -> dict[str, Any]:
    d = found.get("details")
    return d if isinstance(d, dict) else {}


def _markers(details: dict[str, Any]) -> set[str]:
    return {str(m) for m in details.get("root_markers") or [] if isinstance(m, str)}


def _package_json(details: dict[str, Any]) -> dict[str, Any] | None:
    pj = details.get("package_json")
    return pj if isinstance(pj, dict) else None


def _is_mcp(found: dict[str, Any]) -> bool:
    """An MCP connector names itself so; a topic alone is too broad (apps tag "mcp" too)."""
    pkg = found.get("package") if isinstance(found.get("package"), dict) else {}
    words = [str(found.get("name") or ""), str(pkg.get("name") or "")]
    return any(_MCP_RE.search(w) for w in words)


def _tree_counts(details: dict[str, Any]) -> tuple[int, int, int] | None:
    tree = details.get("tree")
    if not isinstance(tree, dict):
        return None
    data, code, files = tree.get("data"), tree.get("code"), tree.get("files", 0)
    if not all(isinstance(n, int) and n >= 0 for n in (data, code, files)):
        return None
    return data, code, files


def _tree_extra(details: dict[str, Any], key: str) -> int:
    """``media`` (pictures) or ``other`` (files that are not data, code, docs or pictures)."""
    tree = details.get("tree")
    n = tree.get(key, 0) if isinstance(tree, dict) else 0
    return n if isinstance(n, int) and not isinstance(n, bool) and n >= 0 else 0


def _tree_is_mostly_data(details: dict[str, Any]) -> bool:
    """Overwhelmingly data: at least 50 data files, and half the tree or twenty per code file.

    Only files with a data ending count as data. A big tree of files in an
    unknown language (scripts, notes) is never "mostly data" by having no code
    files we recognise.
    """
    counts = _tree_counts(details)
    if counts is None:
        return False
    data, code, files = counts
    other = _tree_extra(details, "other")
    if data < 50 or data < 2 * other:
        return False
    return (code == 0 and 2 * data >= files) or data >= 20 * code


def _tree_says_data(details: dict[str, Any]) -> bool:
    """The listed tree says data: data files dominate, or it is a set of pictures.

    Data files must outnumber code files four to one and files of unknown
    kinds (a script in a language not listed is "other") two to one. A tree
    with no code at all is a picture set only when pictures are most of it.
    """
    counts = _tree_counts(details)
    if counts is None:
        return False
    data, code, files = counts
    other = _tree_extra(details, "other")
    media = _tree_extra(details, "media")
    if data >= 3 and data > 4 * code and data >= 2 * other:
        return True
    return code == 0 and media >= 20 and 10 * media >= 7 * files and media >= 2 * other


def _runs_nothing(details: dict[str, Any]) -> bool:
    """The files were looked at and none of them runs: no page, manifest or server file."""
    if details.get("root_markers") is None:
        return False
    markers = _markers(details)
    entry = details.get("entry_html")
    return not (markers & _PROGRAM_MARKERS or markers & SERVER_FILES
                or any(m.endswith(_SERVER_SUFFIXES) for m in markers)
                or _package_json(details) is not None or entry in ENTRY_HTML)


def wants_tree(found: dict[str, Any]) -> bool:
    """Is this GitHub find a data-template candidate worth one (capped) tree listing?

    True for a find from a data-template search, and for a code find whose
    probes found nothing that runs (it can only be data, or nothing we can add).
    """
    if not isinstance(found, dict) or found.get("source") != "github" or _is_mcp(found):
        return False
    return found.get("part") == "data-template" or _runs_nothing(_details(found))


def guess(found: dict[str, Any]) -> str:
    """The mode of *found* (one of :data:`MODES`) from its gathered facts."""
    if not isinstance(found, dict):
        return "unknown"
    if found.get("source") == "hacs":
        return "other"
    if found.get("source") == "mcp" or _is_mcp(found):
        return "mcp_server"
    if found.get("source") == "looks":
        return "theme"
    if found.get("source") == "huggingface":
        return "data_template"
    pkg = found.get("package") if isinstance(found.get("package"), dict) else None
    if found.get("source") in ("npm", "pypi") and pkg is not None:
        if pkg.get("bin"):
            return "cli_tool"
        return "node_lib" if found.get("source") == "npm" else "python_lib"
    details = _details(found)
    markers = _markers(details)
    repo = found.get("repo") if isinstance(found.get("repo"), dict) else {}
    language = str(repo.get("language") or "")
    pj = _package_json(details)
    scripts = {str(s) for s in (pj or {}).get("scripts") or []}
    deps = {str(s) for s in (pj or {}).get("deps") or []}
    has_build = bool(details.get("has_build_script")) or "build" in scripts
    entry = details.get("entry_html")
    entry = entry if entry in ENTRY_HTML else None
    server_files = bool(markers & SERVER_FILES) or any(
        m.endswith(_SERVER_SUFFIXES) for m in markers)

    # 0. A tree that is overwhelmingly data files is a data set, whatever else it has.
    if _tree_is_mostly_data(details):
        return "data_template"
    read = details.get("root_markers") is not None     # the files were actually looked at
    program = bool(markers & _PROGRAM_MARKERS) or server_files or pj is not None
    # 0b. A project whose own name says it holds data, with no program manifest
    #     and no server file, is a data set even when it ships a page to browse
    #     it. Only when a page was found: a project where nothing runs is a data
    #     set only when its listed tree says so (rule 10), never by its name,
    #     its language or its description alone (an empty project has no
    #     language either).
    if read and not program and not _runs_nothing(details) \
            and _DATA_NAME_RE.search(str(found.get("name") or "")):
        return "data_template"
    # 1. Phone and desktop app languages first: their build tools (a Gemfile
    #    for fastlane) look like server files. A container file still means a
    #    server part.
    containers = markers & _CONTAINER_FILES
    if language in APP_LANGUAGES and not containers:
        return "other"
    # 2. A page that opens as it is: an entry page and nothing to build. A page
    #    under docs/ beside server files is the project's manual, not the app.
    #    A page whose own name says it is a template or theme is a look.
    if entry and not has_build and not (entry == "docs/index.html" and server_files):
        names = [str(found.get("name") or "")]
        names += [str(t) for t in (repo.get("topics") or []) if isinstance(t, str)]
        return "theme" if any(_TEMPLATE_RE.search(n) for n in names) else "static_web"
    # 3. A server framework in package.json. Next.js counts as a server: a
    #    static export is not proven without reading its config, so the
    #    stricter answer (not added as a page) wins.
    if pj is not None and deps & SERVER_DEPS:
        return "server_app"
    # 4. Server files, unless a lone Dockerfile sits beside a page that is built
    #    (it only serves the built page). A compose file means more than one
    #    service, so it is a server program whatever else it has.
    page_built = (pj is not None and has_build and entry not in (None, "docs/index.html")
                  and not containers - {"Dockerfile"})
    if server_files and not page_built:
        return "server_app"
    # 5. A page that must be built first.
    if pj is not None and has_build and (markers & set(LOCKFILES) or entry):
        return "web_build"
    # 6. A package with a command.
    if pj is not None and pj.get("bin"):
        return "cli_tool"
    # 7. Languages that mean a server program.
    if language in SERVER_LANGUAGES:
        return "server_app"
    # A JavaScript project with no package.json at its root keeps its parts in
    # folders (most often a server and a page): not something to add as a page.
    if language in ("JavaScript", "TypeScript") and pj is None and "package.json" not in markers \
            and details.get("root_markers") is not None:
        return "server_app"
    if markers & {"go.mod", "Cargo.toml"}:
        readme = str(details.get("readme_text") or "")
        return "server_app" if _SERVER_README_RE.search(readme) else "other"
    # 8. Python.
    if markers & {"pyproject.toml", "setup.py"}:
        return "cli_tool" if details.get("console_scripts") else "python_lib"
    # 9. A JavaScript library.
    if pj is not None:
        return "node_lib"
    # 10. Data files dominate the listed tree (no tree listed: not a data set).
    if _tree_says_data(details):
        return "data_template"
    # 11. Reading limit: a project read at a pinned commit whose
    #     files decide none of the kinds above is something BuildBot cannot
    #     add yet ("other"), not a find it never looked at ("unknown"), so the
    #     plan never says "BuildBot ran out of time to check it" about a
    #     project it did read. A reader that stopped early keeps "unknown"
    #     (github.read_at_pin decides that before asking here).
    if _pinned(repo):
        return "other"
    return "unknown"


def _pinned(repo: dict[str, Any]) -> bool:
    """Was the project read at a pinned commit (its files, not its search answer)?"""
    sha = repo.get("pinned_commit") if isinstance(repo, dict) else None
    return isinstance(sha, str) and bool(re.fullmatch(r"[0-9a-f]{40}", sha))


def integrable(found: dict[str, Any], mode: str | None = None) -> bool:
    """Can BuildBot add this? Pictures by part; HACS and MCP rows never (look-only)."""
    if not isinstance(found, dict):
        return False
    # HACS and MCP rows are look-only; Hugging Face data sets have no downloader
    # in this build, so they are notes, never addable.
    if found.get("source") in ("hacs", "mcp", "huggingface"):
        return False
    if found.get("part") == "pictures":
        return True
    mode = mode if mode is not None else guess(found)
    if mode == "cli_tool" and found.get("source") in ("npm", "pypi"):
        pkg = found.get("package") if isinstance(found.get("package"), dict) else {}
        return bool(pkg.get("bin"))
    return mode in INTEGRABLE_MODES


def candidate_text(row: dict[str, Any]) -> str:
    """``candidate_v1``: ``"<name>. <description> <tags>"``, neutralised, at most 300 chars.

    Accepts a ``found/v1`` record (``name``, ``what_it_is``, ``repo.topics``)
    or a snapshot row mapped to ``name``/``description``/``tags``.

    Noted difference from the pinned formula: that formula calls
    ``neutralise_label`` with its default limit (120) and then cuts at 300, so
    the 300 would never apply. Here the label is neutralised with ``limit=300``
    so the text really is up to 300 characters, which is what the cut says and
    what the meaning check's own fallback (``meaning.candidate_text``) does. Vectors and
    run-time text both come from this one function, so they always agree.
    """
    if not isinstance(row, dict):
        return ""
    name = str(row.get("name") or "")
    description = row.get("description")
    if description is None:
        description = row.get("what_it_is")
    tags = row.get("tags")
    if tags is None:
        repo = row.get("repo") if isinstance(row.get("repo"), dict) else {}
        tags = repo.get("topics") or []
    tag_words = " ".join(str(t) for t in tags if isinstance(t, (str, int)))
    text = f"{name}. {description or ''} {tag_words}"
    return artifact_review.neutralise_label(text, limit=CANDIDATE_TEXT_MAX)[:CANDIDATE_TEXT_MAX]


__all__ = ["APP_LANGUAGES", "CANDIDATE_TEXT_SHAPE", "ENTRY_HTML", "INTEGRABLE_MODES",
           "LOCKFILES", "MODES", "PROGRAM_LANGUAGES", "SERVER_DEPS", "SERVER_FILES",
           "SERVER_LANGUAGES", "candidate_text", "guess", "integrable", "wants_tree"]
