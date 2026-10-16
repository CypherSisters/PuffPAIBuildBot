"""Where to look: one registry of free, accessible sources, searched in a fixed order.

``config/where_to_look.json`` (schema ``puffpai_where_to_look/v1``) lists every
source by the kind of part it can supply. :func:`route` gives the enabled rows
for a part kind, snapshots first. :func:`find_local` searches the snapshots on
this computer and sends nothing. :func:`find_online` asks live sources, and only
ever with ``outbound_query.Sendable`` phrases (a plain string raises), which go
into addresses only through ``outbound_query.url_quote``; when that module is
not in this build, nothing is sent at all. :func:`details` reads a find's
README, licence file and page facts at a pinned commit without spending
GitHub's small "core" allowance, then sets ``mode_guess``, ``integrable`` and
``trust`` (see :mod:`trust_floor`).

Validation of the registry file (a broken file is refused whole, never half
used): unknown fields are refused, hosts must be host names, an adapter must be
``puffpai.catalogs.searchers.<known module>:<function>``, and query templates
may use only ``{words}``, ``{min_stars}``, ``{since}`` and ``{per_page}`` on an
https address whose host is one of the row's own hosts.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import contextlib
import contextvars
import copy
import datetime
import hashlib
import importlib
import inspect
import ipaddress
import json
import os
import re
import string
import threading
import urllib.parse
from typing import Any, Callable, Optional

from puffpai.catalogs import code_mode, search_budget, searchers, trust_floor

SCHEMA = "puffpai_where_to_look/v1"
REGISTRY_NAME = "where_to_look.json"

ROW_FIELDS = ("id", "label", "kinds", "role", "adapter", "snapshot", "query_words_rule",
              "query_template", "hosts", "service", "auth", "key_id", "budget",
              "integrable_modes", "license_field", "robots", "enabled", "default_on",
              "verified_at")
#: One field beyond the pinned list: why a row is switched off (required then, refused otherwise).
OFF_REASON_FIELD = "reason"
#: ``licence_words``: the long licences word by word (see :func:`load_long_editions`).
TOP_FIELDS = frozenset({"schema", "generated_from", "rows", "licence_words"})

KINDS = frozenset({"subject", "code-app", "cli", "data-template", "live-info", "knowledge",
                   "pictures", "look", "web-search", "device"})
ROLES = frozenset({"snapshot", "live", "detail", "trust", "download"})
AUTHS = frozenset({"none", "optional_key", "key"})
ROBOTS = frozenset({"ok", "on_request_only"})
WORD_RULES = frozenset({"two_three", "one_word", "free_text", "exact_name"})
SEARCHER_MODULES = frozenset({"github", "codeberg", "npm", "pypi", "huggingface",
                              "awesome_selfhosted", "hacs", "mcp", "openverse", "commons",
                              "seeds", "knowledge", "looks", "web"})
TEMPLATE_FIELDS = frozenset({"words", "min_stars", "since", "per_page"})
BUDGET_KEYS = frozenset({"pool", "per_min", "per_hour", "per_day", "shared_ip", "with_key"})
MODES = frozenset(code_mode.MODES)

_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,40}$")
_SERVICE_RE = re.compile(r"^[a-z][a-z0-9_]{0,40}$")
_HOST_RE = re.compile(r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
                      r"[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?$")
#: The function part is a public name: no leading underscore, so never a dunder
#: such as ``__init__`` or a private helper.
_ADAPTER_RE = re.compile(r"^puffpai\.catalogs\.searchers\.([a-z_]+):([a-z][a-z0-9_]{0,40})$")
_SNAPSHOT_RE = re.compile(r"^catalog_(?:snapshots|seeds)/[A-Za-z0-9][A-Za-z0-9._-]{0,120}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_STEM_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,100}$")
#: Names Windows keeps for devices ("CON", "nul.json", "COM1.gz"): never a file name here.
_RESERVED_NAME_RE = re.compile(r"^(?:CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|COM[0-9]|LPT[0-9])(?:\..*)?$",
                               re.I)


def _file_name_ok(name: str) -> bool:
    """A plain file name: not a reserved device name, no trailing dot, no ".." anywhere."""
    return (".." not in name and not name.endswith((".", " "))
            and not _RESERVED_NAME_RE.match(name))

#: Finds whose facts :func:`details` reads and whose trust it decides.
_DETAIL_SOURCES = frozenset({"github", "codeberg", "npm", "pypi", "huggingface",
                             "awesome_selfhosted"})
#: Of those, the ones read at a pinned commit (their read facts never come from input).
_PINNED_SOURCES = frozenset({"github", "codeberg"})

NOT_IN_BUILD_LINE ="Looking on {label} is not in this build yet, so nothing was sent there."
NOTHING_SENT_LINE = "Searching online is not in this build yet, so nothing was sent."


class RegistryError(ValueError):
    """The registry file is broken; it is refused whole."""


def _optional(modname: str):
    try:
        return importlib.import_module(modname)
    except ModuleNotFoundError as e:
        if e.name is not None and (e.name == modname or modname.startswith(e.name + ".")):
            return None          # not in this build yet
        raise                    # exists but broken: fail loudly


def _config_dir() -> str:
    from puffpai.catalogs import live
    return os.path.join(live.repo_root(), "config")


def registry_path() -> str:
    return os.path.join(_config_dir(), REGISTRY_NAME)


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------

def _host_ok(host: Any) -> bool:
    if not isinstance(host, str) or host != host.lower() or not _HOST_RE.match(host):
        return False
    try:
        ipaddress.ip_address(host)
        return False
    except ValueError:
        return True


def _template_problem(template: str, hosts: list[str]) -> Optional[str]:
    try:
        parts = list(string.Formatter().parse(template))
    except ValueError:
        return "query_template does not parse"
    for _lit, field, spec, conv in parts:
        if field is None:
            continue
        if field not in TEMPLATE_FIELDS or spec or conv:
            return f"query_template uses {{{field}}}, which is not allowed"
    probe = template.format(words="w", min_stars=5, since="2026-01-01", per_page=10)
    split = urllib.parse.urlsplit(probe)
    if split.scheme != "https" or split.port is not None or split.username or split.password:
        return "query_template must be a plain https address"
    if (split.hostname or "") not in hosts:
        return "query_template host is not one of the row's hosts"
    return None


def row_problems(row: Any) -> list[str]:
    """Every reason *row* is refused ([] when it is fine)."""
    if not isinstance(row, dict):
        return ["a row is not an object"]
    probs = []
    allowed = set(ROW_FIELDS) | {OFF_REASON_FIELD}
    unknown = sorted(set(row) - allowed)
    if unknown:
        probs.append(f"unknown fields: {', '.join(unknown)}")
    missing = [f for f in ROW_FIELDS if f not in row]
    if missing:
        probs.append(f"missing fields: {', '.join(missing)}")
        return probs
    if not isinstance(row["id"], str) or not _ID_RE.match(row["id"]):
        probs.append("bad id")
    if not isinstance(row["label"], str) or not 0 < len(row["label"]) <= 80:
        probs.append("bad label")
    kinds = row["kinds"]
    if not isinstance(kinds, list) or not kinds or any(k not in KINDS for k in kinds):
        probs.append("bad kinds")
    if row["role"] not in ROLES:
        probs.append("bad role")
    enabled = row["enabled"]
    if not isinstance(enabled, bool) or not isinstance(row["default_on"], bool):
        probs.append("enabled and default_on must be true or false")
    if enabled is False:
        if not isinstance(row.get(OFF_REASON_FIELD), str) or not row[OFF_REASON_FIELD].strip():
            probs.append("a row that is off needs a reason")
    elif OFF_REASON_FIELD in row:
        probs.append("reason is only for rows that are off")
    adapter = row["adapter"]
    if adapter is None:
        if enabled is not False:
            probs.append("an enabled row needs an adapter")
    else:
        m = _ADAPTER_RE.match(adapter) if isinstance(adapter, str) else None
        if not m or m.group(1) not in SEARCHER_MODULES:
            probs.append("unknown adapter")
    hosts = row["hosts"]
    if not isinstance(hosts, list) or any(not _host_ok(h) for h in hosts):
        probs.append("hosts must be host names")
        hosts = []
    if row["role"] == "snapshot":
        if hosts:
            probs.append("a snapshot row sends nothing, so it has no hosts")
        if not isinstance(row["snapshot"], str) or not _SNAPSHOT_RE.match(row["snapshot"]) \
                or not _file_name_ok(row["snapshot"].rsplit("/", 1)[-1]):
            probs.append("bad snapshot")
    elif row["snapshot"] is not None:
        probs.append("only snapshot rows name a snapshot")
    elif row["role"] == "live" and enabled is not False and not hosts:
        probs.append("a live row needs hosts")
    template = row["query_template"]
    if template is not None:
        if not isinstance(template, str):
            probs.append("bad query_template")
        else:
            why = _template_problem(template, hosts)
            if why:
                probs.append(why)
    if row["query_words_rule"] is not None and row["query_words_rule"] not in WORD_RULES:
        probs.append("bad query_words_rule")
    if row["service"] is not None and (not isinstance(row["service"], str)
                                       or not _SERVICE_RE.match(row["service"])):
        probs.append("bad service")
    if row["auth"] not in AUTHS:
        probs.append("bad auth")
    if row["key_id"] is not None and (not isinstance(row["key_id"], str)
                                      or not _ID_RE.match(row["key_id"])):
        probs.append("bad key_id")
    if row["auth"] in ("optional_key", "key") and not row["key_id"]:
        probs.append("a keyed row needs a key_id")
    budget = row["budget"]
    if budget is not None and (not isinstance(budget, dict) or set(budget) - BUDGET_KEYS):
        probs.append("bad budget")
    modes = row["integrable_modes"]
    if not isinstance(modes, list) or any(m not in MODES for m in modes):
        probs.append("bad integrable_modes")
    if row["license_field"] is not None and not isinstance(row["license_field"], str):
        probs.append("bad license_field")
    if row["robots"] not in ROBOTS:
        probs.append("bad robots")
    if not isinstance(row["verified_at"], str) or not _DATE_RE.match(row["verified_at"]):
        probs.append("bad verified_at")
    return probs


def validate(data: Any) -> list[dict[str, Any]]:
    """The rows of a registry document, or :class:`RegistryError` naming every problem."""
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        raise RegistryError("not a where_to_look/v1 registry")
    extra = set(data) - TOP_FIELDS
    if extra:
        raise RegistryError(f"unknown fields: {', '.join(sorted(extra))}")
    if "licence_words" in data and not isinstance(data["licence_words"], dict):
        raise RegistryError("licence_words must be an object")
    rows = data.get("rows")
    if not isinstance(rows, list) or not rows:
        raise RegistryError("no rows")
    problems, seen = [], set()
    for i, row in enumerate(rows):
        for p in row_problems(row):
            problems.append(f"row {i} ({row.get('id') if isinstance(row, dict) else '?'}): {p}")
        if isinstance(row, dict):
            if row.get("id") in seen:
                problems.append(f"row {i}: duplicate id")
            seen.add(row.get("id"))
    if problems:
        raise RegistryError("; ".join(problems[:20]))
    return rows


_REG_LOCK = threading.Lock()
_REG_CACHE: dict[str, Any] = {"key": None, "rows": None}


def load_registry(path: Optional[str] = None) -> list[dict[str, Any]]:
    target = path or registry_path()
    with open(target, encoding="utf-8") as fh:
        return validate(json.load(fh))


def registry() -> list[dict[str, Any]]:
    """Every row of the shipped registry (a copy). Raises when the file is broken."""
    path = registry_path()
    key = (path, os.path.getmtime(path))
    with _REG_LOCK:
        if _REG_CACHE["key"] != key:
            _REG_CACHE.update(key=key, rows=load_registry(path))
        return copy.deepcopy(_REG_CACHE["rows"])


def _route_all(part_kind: str) -> list[dict[str, Any]]:
    """Every enabled snapshot and live row for *part_kind*, searcher present or not."""
    rows = [r for r in registry() if r["enabled"] and part_kind in r["kinds"]
            and r["role"] in ("snapshot", "live")]
    return [r for r in rows if r["role"] == "snapshot"] + [r for r in rows if r["role"] == "live"]


def key_ready(row: dict[str, Any]) -> bool:
    """A row that needs the owner's own key (``auth: "key"``) is asked only
    while that key is in the vault and works; any other row needs no key."""
    if row.get("auth") != "key":
        return True
    keys = _optional("puffpai.security.keys")
    key_id = row.get("key_id")
    if keys is None or not isinstance(key_id, str) or not key_id:
        return False
    try:
        return (keys.key_status(key_id) or {}).get("state") == "works"
    except Exception:  # noqa: BLE001 - a key that cannot be checked is not used
        return False


def route(part_kind: str) -> list[dict[str, Any]]:
    """Enabled snapshot and live rows for *part_kind*: snapshots first, registry order kept.

    Only rows whose searcher is in this build, and a row that needs the
    owner's own key only while that key works: a place BuildBot cannot ask is
    never named as a place it asks (the "Start looking" intro reads this list).
    """
    return [r for r in _route_all(part_kind) if adapter_fn(r) is not None and key_ready(r)]


def row_by_id(row_id: str) -> Optional[dict[str, Any]]:
    for r in registry():
        if r["id"] == row_id:
            return r
    return None


def adapter_fn(row: dict[str, Any]) -> Optional[Callable]:
    """The row's adapter function, or None when its module is not in this build yet."""
    m = _ADAPTER_RE.match(str(row.get("adapter") or ""))
    if not m or m.group(1) not in SEARCHER_MODULES:
        return None
    modname = "puffpai.catalogs.searchers." + m.group(1)
    mod = _optional(modname)
    fn = getattr(mod, m.group(2), None) if mod is not None else None
    # Only a plain function written in that very module: never a class, a
    # module attribute, or a helper the module merely imported.
    if not inspect.isfunction(fn) or getattr(fn, "__module__", None) != modname:
        return None
    return fn


# ---------------------------------------------------------------------------
# searching
# ---------------------------------------------------------------------------

def _home_assistant_configured() -> bool:
    try:
        from puffpai.config import get_config
        ha = getattr(get_config(), "home_assistant", None)
        return bool(getattr(ha, "url", None) or getattr(ha, "base_url", None))
    except Exception:
        return False


def find_local(job: dict, *, kind: str, limit_per_source: int = 25) -> list[dict]:
    """``found/v1`` records from the snapshots for *kind*. Sends nothing."""
    if not isinstance(job, dict):
        return []
    out: list[dict] = []
    seen: set[str] = set()
    for row in route(kind):
        if row["role"] != "snapshot":
            continue
        if row["id"] == "hacs" and not _home_assistant_configured():
            continue
        fn = adapter_fn(row)
        if fn is None:
            continue
        for rec in fn(job, kind=kind, row=row, limit=limit_per_source) or []:
            if rec.get("id") not in seen:
                seen.add(rec.get("id"))
                out.append(rec)
    return out


def find_online(job: dict, phrases: list, *, kind: str, budget,
                progress: Optional[Callable[[str], None]] = None) -> list[dict]:
    """``found/v1`` records from live sources for *kind*, only after "Start looking".

    Every phrase must be an ``outbound_query.Sendable`` (a plain string raises
    ``TypeError``). With ``job["id"] == "subject"`` and ``kind == "subject"``
    the two subject queries run. When the outbound screen is not in this build,
    nothing is sent and the answer is ``[]``.
    """
    oq = _optional("puffpai.security.outbound_query")
    if oq is None:
        if progress:
            progress(NOTHING_SENT_LINE)
        return []
    phrases = list(phrases or [])
    for p in phrases:
        if not oq.is_sendable(p):
            raise TypeError("find_online takes only Sendable phrases from the outbound screen")
    if not phrases or not isinstance(job, dict):
        return []
    if kind == "subject" and job.get("id") != "subject":
        raise ValueError("the subject queries run only for the subject job")
    calls: list[tuple[dict, Callable]] = []
    for row in _route_all(kind):
        if row["role"] != "live" or not _asks(row, job):
            continue
        fn = adapter_fn(row)
        if fn is None:
            # Said once per build (the budget is one per build), never per job.
            if progress and _first_telling(budget, "not_in_build:" + row["id"]):
                progress(NOT_IN_BUILD_LINE.format(label=row["label"]))
            continue
        calls.append((row, fn))
    out: list[dict] = []
    seen: set[str] = set()
    for got in _ask_all(calls, job, phrases, kind=kind, budget=budget, progress=progress):
        for rec in got:
            if rec.get("id") not in seen:
                seen.add(rec.get("id"))
                out.append(rec)
    return out


#: How many places one job's search asks at once. Each place is a different
#: site with its own pace (the budget paces each one, under its lock), so
#: asking them together takes as long as the slowest, not the sum (so
#: 20 finds per job are read in what is left of 90 s).
FIND_WORKERS = 6


def _ask_all(calls: list, job: dict, phrases: list, *, kind: str, budget,
             progress: Optional[Callable[[str], None]]) -> list[list[dict]]:
    """Every searcher in *calls* asked at once; their answers in *calls* order.

    The caller's deadline (:class:`searchers.deadline`) holds in every worker.
    Progress lines are passed on as they come, one at a time. A searcher that
    fails makes the whole search fail as it did one by one: the first failure,
    in *calls* order, is raised once every searcher has stopped.
    """
    import concurrent.futures

    if not calls:
        return []
    at = getattr(searchers._DEADLINE, "at", None)
    lock = threading.Lock()

    def say(line: str) -> None:
        with lock:
            progress(line)

    def one(row: dict, fn: Callable) -> list[dict]:
        with searchers.deadline(at):
            return list(fn(job, phrases, kind=kind, budget=budget, row=row,
                           progress=say if progress else None) or [])

    if len(calls) == 1:
        return [one(*calls[0])]
    n = max(1, min(FIND_WORKERS, len(calls)))
    with concurrent.futures.ThreadPoolExecutor(max_workers=n, thread_name_prefix="where-find") as pool:
        futures = [pool.submit(one, row, fn) for row, fn in calls]
        concurrent.futures.wait(futures)
    return [f.result() for f in futures]         # the first failure, in order, is raised here


#: Live rows asked only for a "learn about" job.
_LEARN_ONLY = frozenset({"wikipedia", "wikibooks"})


def _asks(row: dict[str, Any], job: Any) -> bool:
    """Would :func:`find_online` ask this live row for *job*? (Searcher aside.)"""
    if row["id"] in _LEARN_ONLY and not (isinstance(job, dict) and job.get("learn")):
        return False
    return key_ready(row)


def live_rows_for(job: dict, kind: str) -> list[dict[str, Any]]:
    """The live rows :func:`find_online` really asks for *job* and *kind*, in order.

    The same rules as the search itself (the searcher is in this build, a
    learn-only place only for a learn job, her own key only while it works),
    so words that name the places asked ("Start looking" asks ...) can use
    this and never name a place that will not be asked.
    """
    return [r for r in _route_all(kind) if r["role"] == "live" and _asks(r, job)
            and adapter_fn(r) is not None]


def would_send(row: dict[str, Any], job: Any, phrases: list, kind: str) -> bool:
    """Would *row*'s searcher send at least one of *phrases* for *job* and *kind*?

    Sends nothing. A searcher that sends only some phrases (PyPI: exact
    package names; Wikipedia: a topic it can make a book of) says so through
    its own ``would_send``, built from the same rule its search uses; any
    other searcher sends when it has a phrase. A searcher whose check fails
    is counted as not asked (the words then never name it).
    """
    phrases = list(phrases or [])
    if not phrases:
        return False
    fn = adapter_fn(row)
    if fn is None:
        return False
    mod = inspect.getmodule(fn)
    check = getattr(mod, "would_send", None) if mod is not None else None
    if check is None:
        return True
    if not inspect.isfunction(check) or check.__module__ != fn.__module__:
        return False
    try:
        return bool(check(job, phrases, kind=kind, row=row))
    except Exception:  # noqa: BLE001 - a check that fails names nothing
        return False


def asked_rows(job: dict, phrases: list, kind: str) -> list[dict[str, Any]]:
    """The live rows :func:`find_online` really sends something to for *job*, *kind*, *phrases*."""
    return [r for r in live_rows_for(job, kind) if would_send(r, job, phrases, kind)]


def asked_labels(needs: Any, sendables: list, *, kinds: Optional[Any] = None) -> list[str]:
    """The labels of the places "Start looking" really asks, in registry order, once each.

    *needs* is the needs/v1 record: only ticked jobs count, each with the
    Sendables screened for it (``source_hint`` is the job's id) and only the
    part kinds that are searched (*kinds*; None: every kind the job has). The
    subject Sendable asks the subject rows. A place is named only when its
    searcher would send one of those phrases (PyPI is not named for every
    job: it asks only for an exact package name).
    """
    wanted = None if kinds is None else {str(k) for k in kinds}
    labels: list[str] = []

    def add(rows: list[dict[str, Any]]) -> None:
        for r in rows:
            label = str(r.get("label") or r.get("id") or "")
            if label and label not in labels:
                labels.append(label)

    sendables = [s for s in sendables or [] if isinstance(getattr(s, "phrase", None), str)]
    subject = [s for s in sendables if getattr(s, "subject", False)
               or getattr(s, "source_hint", None) == "subject"]
    jobs = needs.get("jobs") if isinstance(needs, dict) else None
    jobs = [j for j in jobs if isinstance(j, dict)] if isinstance(jobs, list) else []
    # The subject is searched only alongside a ticked job that is searched.
    if subject and any(j.get("ticked") and any(
            isinstance(p, dict) and (wanted is None or p.get("kind") in wanted)
            for p in j.get("parts") or []) for j in jobs):
        add(asked_rows({"id": "subject", "name": subject[0].phrase}, subject[:1], "subject"))
    for job in jobs:
        if not job.get("ticked"):
            continue
        mine = [s for s in sendables if s not in subject
                and str(getattr(s, "source_hint", "")) == str(job.get("id"))]
        if not mine:
            continue
        kinds_here: list[str] = []
        for part in job.get("parts") or []:
            kind = part.get("kind") if isinstance(part, dict) else None
            if isinstance(kind, str) and kind not in kinds_here                     and (wanted is None or kind in wanted):
                kinds_here.append(kind)
        for kind in kinds_here:
            add(asked_rows(job, mine, kind))
    return labels


_TOLD_LOCK = threading.Lock()


def _first_telling(budget, key: str) -> bool:
    """True the first time *key* is told during this build (one budget = one build)."""
    with _TOLD_LOCK:
        told = getattr(budget, "told", None)
        if not isinstance(told, set):
            try:
                told = set()
                budget.told = told
            except (AttributeError, TypeError):
                return True
        if key in told:
            return False
        told.add(key)
        return True


#: Detail readers per source: the registry row that reads a find at a pinned commit.
_DETAIL_ROWS = {"github.com": "github_details", "codeberg.org": "codeberg_details"}
#: Sources whose finds name a project on a code host that is read at a pinned commit.
_HOSTED_SOURCES = frozenset({"github", "codeberg", "awesome_selfhosted"})


#: Package sources and the registry row that reads their facts again.
_PACKAGE_DETAIL_ROWS = {"npm": "npm_details", "pypi": "pypi_details"}


def _detail_row_id(rec: dict) -> Optional[str]:
    if rec.get("source") in _PACKAGE_DETAIL_ROWS:
        return _PACKAGE_DETAIL_ROWS[str(rec["source"])]
    if rec.get("source") not in _HOSTED_SOURCES:
        return None
    repo = rec.get("repo") if isinstance(rec.get("repo"), dict) else {}
    return _DETAIL_ROWS.get(str(repo.get("host") or ""))


#: Sources whose finds come from a list shipped with BuildBot or from a search
#: whose rows are never added as code: their mode, ``integrable`` and ``trust``
#: are fixed by the source, never read back from the record (a HACS or MCP
#: record with a forged ``integrable: true`` and a forged pre-tickable trust
#: must not come back from details() unchanged).
_FIXED_SOURCES: dict[str, tuple[str, dict[str, Any]]] = {
    "hacs": ("other", {"floor_ok": False, "path": "", "pretick_ok": False,
                       "reasons": ["look_only"]}),
    "mcp": ("mcp_server", {"floor_ok": True, "path": "", "pretick_ok": False, "reasons": []}),
    "looks": ("theme", {"floor_ok": True, "path": "", "pretick_ok": False, "reasons": []}),
}
#: A web search result is a link to look at, never a part.
_FIXED_SOURCES["web"] = ("other", {"floor_ok": False, "path": "", "pretick_ok": False,
                                   "reasons": ["look_only"]})
for _src in ("seeds", "public_apis", "apis_guru", "kiwix", "wikipedia", "gutenberg", "openstax"):
    _FIXED_SOURCES[_src] = ("other", {"floor_ok": True, "path": "", "pretick_ok": False,
                                      "reasons": []})
del _src
#: The only sources whose finds are pictures.
_PICTURE_SOURCES = frozenset({"commons", "openverse"})
_THUMB_NAME_RE = re.compile(r"^[0-9a-f]{64}\.jpg$")


def _fixed(rec: dict) -> dict:
    """*rec* with the facts its source fixes; an unknown source is never trusted."""
    mode, trust = _FIXED_SOURCES.get(str(rec.get("source")), (
        "unknown", {"floor_ok": False, "path": "", "pretick_ok": False,
                    "reasons": ["unknown_source"]}))
    rec["mode_guess"] = mode
    rec["integrable"] = False
    rec["trust"] = copy.deepcopy(trust)
    return rec


def _untrusted(rec: dict, reason: str) -> dict:
    rec["mode_guess"] = "unknown"
    rec["integrable"] = False
    rec["trust"] = {"floor_ok": False, "path": "", "pretick_ok": False, "reasons": [reason]}
    return rec


def _checked_thumb(rec: dict) -> None:
    """Keep a picture's ``thumb_local`` only when it is one of BuildBot's own thumbnails."""
    pic = rec.get("picture")
    if not isinstance(pic, dict) or pic.get("thumb_local") is None:
        return
    path = pic.get("thumb_local")
    ok = False
    if isinstance(path, str) and _THUMB_NAME_RE.match(os.path.basename(path)):
        folder = os.path.normcase(os.path.realpath(searchers.thumbs_dir()))
        real = os.path.normcase(os.path.realpath(path))
        ok = os.path.dirname(real) == folder and os.path.isfile(real)
    if not ok:
        pic["thumb_local"] = None


def details(found: dict, *, budget) -> dict:
    """A copy of *found* with its details read and ``mode_guess``, ``integrable``, ``trust`` set.

    Nothing that only this function may decide is ever kept from *found*:
    finds from a shipped list or a look-only source get the facts their source
    fixes (never integrable), a picture must come from a picture source, and
    a find from an unknown source is not trusted at all.
    """
    rec = copy.deepcopy(found) if isinstance(found, dict) else {}
    source = rec.get("source")
    if rec.get("part") == "pictures" and source not in _PICTURE_SOURCES:
        return _untrusted(rec, "not_a_picture_source")
    if source in _PICTURE_SOURCES and rec.get("part") != "pictures":
        return _untrusted(rec, "not_a_picture")
    if source not in _DETAIL_SOURCES and source not in _PICTURE_SOURCES:
        return _fixed(rec)   # snapshot rows (live info, knowledge, looks, look-only)
    if source in _PICTURE_SOURCES:
        # A picture is integrable by its part and trusted by its picture facts
        # only: code facts that came in with it (details, a repo, a package, a
        # mode) are never read, so none is kept (a forged
        # "static_web" mode and forged page details are dropped).
        _checked_thumb(rec)
        rec["details"] = None
        rec["repo"] = None
        rec["package"] = None
        rec["mode_guess"] = "other"
        rec["integrable"] = code_mode.integrable(rec, "other")
        rec["trust"] = _no_pretick(trust_floor.trust_record(rec), rec)
        return rec
    # A mode that came in with the record is never trusted: it is decided here,
    # by the details reader (which sees facts kept out of found/v1) or by guess().
    rec["mode_guess"] = "unknown"
    pinned_source = rec.get("source") in _PINNED_SOURCES or (
        rec.get("source") in _HOSTED_SOURCES and _detail_row_id(rec) is not None)
    if rec.get("source") in _DETAIL_SOURCES:
        # Neither are facts that only a read at a pinned commit (or the source's
        # own reader) may give: they are dropped and read again here, or stay
        # empty. Never kept from input, whatever the source (an awesome-selfhosted
        # or Hugging Face record can carry a forged pin).
        repo = rec.get("repo")
        if isinstance(repo, dict) or pinned_source:
            repo = repo if isinstance(repo, dict) else {}
            rec["repo"] = repo
            for key in ("pinned_commit", "pinned_at", "license_read", "license_check",
                        "license_check_why"):
                repo[key] = None
        rec["details"] = searchers.empty_details()
        pkg = rec.get("package")
        if isinstance(pkg, dict):
            # Package facts the trust floor uses are read again by the source's reader.
            pkg.update(downloads_monthly=None, bin=[], repo_name_matches=None)
    detail_row = _detail_row_id(rec)
    if detail_row:
        row = row_by_id(detail_row)
        fn = adapter_fn(row) if row and row["enabled"] else None
        if fn is not None and budget.take("details"):
            rec = fn(rec, budget=budget, row=row)
    read_mode = rec.get("mode_guess")
    if pinned_source and not (rec.get("repo") or {}).get("pinned_commit"):
        mode = "unknown"          # nothing was read at a pinned commit in this call
    elif pinned_source:
        # The reader decided with every fact it saw; its "unknown" (probing
        # cut short) is never guessed over from the fewer facts kept here.
        mode = read_mode if read_mode in code_mode.MODES else "unknown"
    elif read_mode in code_mode.MODES and read_mode != "unknown":
        mode = read_mode
    else:
        mode = code_mode.guess(rec)
    rec["mode_guess"] = mode
    rec["integrable"] = code_mode.integrable(rec, mode)
    trust = trust_floor.trust_record(rec)
    if pinned_source and not (rec.get("repo") or {}).get("pinned_commit"):
        # Nothing was read at a pinned commit in this call, so no fact the
        # trust floor would use has been checked here (forged stars on a
        # find whose read failed must not give the "stars" path and a
        # pre-tick).
        trust = {"floor_ok": False, "path": "", "pretick_ok": False,
                 "reasons": [str(r) for r in trust.get("reasons") or []] + [NOT_READ]}
    rec["trust"] = _no_pretick(trust, rec)
    return rec


#: The trust reason for a code find that could not be read at a pinned commit.
NOT_READ = "not_read"


def _no_pretick(trust: dict, rec: dict) -> dict:
    """Never pre-ticked unless it can be added: a find that is not integrable is not."""
    if not rec.get("integrable"):
        trust["pretick_ok"] = False
    return trust


#: How many finds are read at once by :func:`details_many` (8 read
#: only 47 of 60 finds in 60 s live; every reader waits on the network, not
#: on this computer, and each file host is paced by the budget).
DETAIL_WORKERS = 12
#: How long before its deadline :func:`details_many` sends its last request,
#: so an answer on its way can still arrive before the deadline itself.
DEADLINE_GRACE_S = 1.0


def details_many(founds: list, *, budget, workers: int = DETAIL_WORKERS,
                 deadline: Optional[float] = None) -> list:
    """:func:`details` for many finds at once, in the same order.

    Reading a find is several small answers one after another, so a plan that
    looks closely at 20 finds per job cannot read them one by one inside the
    search time. The same budget caps apply (they are counted under a lock).
    A find not read (an error, or *deadline*, a ``time.monotonic()`` value,
    passed before its turn) comes back as it went in, untouched: never with
    facts it did not earn.

    With a deadline this returns by the deadline itself, whatever the
    readers are doing: no request is sent in its last
    :data:`DEADLINE_GRACE_S`, a find still being read at the deadline comes
    back untouched, and its reader stops at its next request (so a slow
    file never holds the whole pool, and the search, past the deadline).
    """
    import concurrent.futures
    import time as _time

    items = list(founds or [])
    out: list = [copy.deepcopy(f) if isinstance(f, dict) else f for f in items]
    if not items:
        return out

    last_send = None if deadline is None else deadline - DEADLINE_GRACE_S

    def one(i: int):
        if last_send is not None and _time.monotonic() > last_send:
            return i, None
        try:
            # No new request after the last send time, even for a find already
            # started: it is finished with the facts read so far (fewer facts,
            # never more trust).
            with searchers.deadline(last_send):
                return i, details(items[i], budget=budget)
        except Exception:  # noqa: BLE001 - one find that cannot be read is left as it was
            return i, None

    n = max(1, min(int(workers or 1), 16, len(items)))
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=n, thread_name_prefix="where-details")
    try:
        futures = [pool.submit(one, i) for i in start_order(items)]
        timeout = None
        if deadline is not None:
            # A little before the deadline, so the answers are in by it.
            timeout = max(0.0, deadline - _time.monotonic() - 0.05)
        done, _late = concurrent.futures.wait(futures, timeout=timeout)
        for fut in done:
            try:
                i, got = fut.result()
            except Exception:  # noqa: BLE001 - as above
                continue
            if isinstance(got, dict):
                out[i] = got
    finally:
        # Never wait here for a reader that is late: finds not yet started are
        # dropped, a find being read finishes on its own and is not used.
        pool.shutdown(wait=False, cancel_futures=True)
    return out


def _slow_read(found: Any) -> bool:
    """Is this find read in many turns on a shared file host (file by file),
    or from a large archive? (What its search answer says; nothing is asked.)"""
    if not isinstance(found, dict):
        return False
    repo = found.get("repo") if isinstance(found.get("repo"), dict) else {}
    if found.get("source") == "codeberg":
        return True
    if found.get("source") != "github":
        return False
    try:
        from puffpai.catalogs.searchers import github as _gh
        return not _gh.small_enough(repo) or _gh.big_archive(repo)
    except Exception:  # noqa: BLE001 - unknown: read in its place
        return False


def start_order(founds: list) -> list[int]:
    """The order :func:`details_many` starts reading in: finds read in many
    turns first, then the rest, each group in the order given (the results
    stay in the order given).

    A find read file by file waits its turn on the file host (4 a second,
    source_fetch's table) for 8 to 14 files, while an archive is one turn on
    another host. Started last, those finds were the ones the 90 s cut (live, at once a second, 3 jobs: 17, 20 and 15
    of 20 read); started first, their turns spread over
    the whole reading time while the archives fill the other host.
    """
    return sorted(range(len(founds)), key=lambda i: (0 if _slow_read(founds[i]) else 1, i))


#: How many finds per job a build reads closely (the bar: 20 or more).
READ_PER_JOB = 20


def _may_pass_floor(found: dict) -> bool:
    """False only for a GitHub find that GitHub says has no licence file and
    that no shipped list names: reading it cannot give it a licence, so it
    cannot pass the trust floor whatever else is read."""
    repo = found.get("repo") if isinstance(found.get("repo"), dict) else {}
    if repo.get("host") != "github.com" or "license_reported" not in repo \
            or repo.get("license_reported") is not None:
        return True
    try:
        return bool(trust_floor.listed_lookup(str(found.get("url") or "")))
    except Exception:  # noqa: BLE001 - no list: judged by the licence alone
        return False


def read_order(finds_by_job: Any, *, per_job: int = READ_PER_JOB) -> list[dict]:
    """The finds a whole build reads closely, in the order to read them.

    *finds_by_job* is ``[(job_id, [find, ...])]`` (or a dict, in job order),
    each job's finds best first. The first find of every job comes first,
    then the second of every job, and so on, at most *per_job* per job; a
    find listed under two jobs is read once. Given to :func:`details_many`
    with one deadline for the build, every job has its best finds read
    before any job has its twentieth (reading is paced per host, so a build
    of many jobs may not reach 20 each inside the search time).
    """
    items = list(finds_by_job.items()) if isinstance(finds_by_job, dict) else list(finds_by_job or [])
    # Within a job, a find that can still pass the trust floor is read before
    # one that cannot, the job's own order kept otherwise.
    lists = [sorted([f for f in (finds or []) if isinstance(f, dict)],
                    key=lambda f: not _may_pass_floor(f))[:max(0, int(per_job))]
             for _job, finds in items]
    out: list[dict] = []
    seen: set[str] = set()
    for rank in range(max((len(x) for x in lists), default=0)):
        for finds in lists:
            if rank < len(finds):
                key = str(finds[rank].get("id") or id(finds[rank]))
                if key not in seen:
                    seen.add(key)
                    out.append(finds[rank])
    return out


def last_changed_words(found: Any) -> Optional[str]:
    """``"Last changed 2019."`` from a find's last change date, or None when unknown."""
    repo = found.get("repo") if isinstance(found, dict) else None
    when = repo.get("pushed_at") if isinstance(repo, dict) else None
    if not isinstance(when, str) or not re.match(r"^\d{4}-\d{2}-\d{2}", when):
        return None
    try:
        day = datetime.date.fromisoformat(when[:10])
    except ValueError:
        return None
    if day.year < 2000 or day > today() + datetime.timedelta(days=2):
        return None
    return f"Last changed {day.year}."


def new_budget(*, wizard: bool) -> "search_budget.SearchBudget":
    return search_budget.new_budget(wizard=wizard)


# ---------------------------------------------------------------------------
# licences
# ---------------------------------------------------------------------------

#: Exact licence ids (upper case, spaces as "-"), matched whole: a name that
#: only starts like an open licence ("UNLICENSED", "MITx", "BSD-Protection")
#: is not one. Anything not listed here is not usable (None), which is the
#: safe answer for an unknown licence.
_FAMILY_IDS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("permissive", re.compile(
        r"MIT|MIT-0|APACHE|APACHE-2\.0|BSD|0BSD|BSD-1-CLAUSE|BSD-2-CLAUSE|BSD-3-CLAUSE|"
        r"BSD-3-CLAUSE-CLEAR|ISC|UNLICENSE|THE-UNLICENSE|CC0|CC0-1\.0|ZLIB|WTFPL|"
        # Pictures: Commons says "Public domain", Openverse "pdm" (the Public Domain Mark).
        r"PUBLIC-DOMAIN|PDM|PDM-1\.0")),
    ("copyleft", re.compile(
        r"GPL|AGPL|LGPL|MPL|"
        r"GPL-[23]\.0(?:-ONLY|-OR-LATER|\+)?|AGPL-3\.0(?:-ONLY|-OR-LATER|\+)?|"
        r"LGPL-(?:2\.0|2\.1|3\.0)(?:-ONLY|-OR-LATER|\+)?|MPL-2\.0")),
    ("credit", re.compile(
        r"CC-BY|CC-BY-SA|CC-BY(?:-SA)?-(?:1\.0|2\.0|2\.5|3\.0|4\.0)|"
        r"OFL|OFL-1\.[01](?:-RFN|-NO-RFN)?")),
    # "any NC" (spec table): with ShareAlike or NoDerivatives it is still personal use only.
    ("personal", re.compile(r"CC-BY-NC(?:-SA|-ND)?(?:-(?:1\.0|2\.0|2\.5|3\.0|4\.0))?")),
)
#: SPDX exceptions that only ever add permission (``GPL-2.0 WITH Classpath-exception-2.0``).
#: Any other ``WITH`` (for example "Commons-Clause") makes the whole expression unusable.
_EXCEPTIONS = re.compile(
    r"CLASSPATH-EXCEPTION-2\.0|LLVM-EXCEPTION|GCC-EXCEPTION-(?:2\.0|3\.1)|"
    r"AUTOCONF-EXCEPTION-(?:2\.0|3\.0)|BISON-EXCEPTION-2\.2|FONT-EXCEPTION-2\.0|"
    r"OPENJDK-ASSEMBLY-EXCEPTION-1\.0|LGPL-3\.0-LINKING-EXCEPTION|UNIVERSAL-FOSS-EXCEPTION-1\.0")
#: The longest licence text :func:`license_words` reads (longer is None, never cut).
LICENSE_WORDS_MAX = 200
_RANK = {"permissive": 0, "credit": 1, "copyleft": 2, "personal": 3}
_WORDS = {
    "permissive": "Free to use and change ({id}).",
    "copyleft": "Free to use and change; if you share changes, they must stay open ({id}).",
    "credit": "Free to use with credit ({id}).",
    "personal": "Free for personal use only ({id}).",
}


def _norm(one: str) -> str:
    norm = re.sub(r"\s+", "-", one.strip().upper())
    norm = re.sub(r"(?:^|-)LICEN[SC]E$", "", norm)      # "MIT License" is MIT
    return re.sub(r"^LICEN[SC]E-", "", norm)


def _family(one: str) -> Optional[str]:
    norm = _norm(one)
    for fam, pattern in _FAMILY_IDS:
        if pattern.fullmatch(norm):
            return fam
    return None


def _expression(text: str) -> Optional[list[str]]:
    """The licence ids in an SPDX expression (``A OR (B AND C)``), or None when malformed.

    ``WITH <exception>`` is allowed only for the exceptions in :data:`_EXCEPTIONS`.
    """
    tokens = re.findall(r"\(|\)|[^\s()]+", text)
    ids: list[str] = []
    depth, want_id, i = 0, True, 0
    while i < len(tokens):
        tok = tokens[i]
        up = tok.upper()
        if tok == "(":
            if not want_id:
                return None
            depth += 1
        elif tok == ")":
            if want_id or depth == 0:
                return None
            depth -= 1
        elif up in ("AND", "OR"):
            if want_id:
                return None
            want_id = True
        elif up == "WITH":
            if want_id or i + 1 >= len(tokens) or not _EXCEPTIONS.fullmatch(tokens[i + 1].upper()):
                return None
            i += 1
        else:
            if not want_id:
                return None
            ids.append(tok)
            want_id = False
        i += 1
    if want_id or depth != 0 or not ids:
        return None
    return ids


def license_words(spdx_or_family: str) -> Optional[str]:
    """The plain licence line for an SPDX id, expression or family name; None = not usable.

    Ids are matched whole against a fixed list (unknown means None). In an
    expression every id must be usable and the strictest one decides the line.
    The whole text is read: nothing is cut before parsing, so an unusable id
    late in a long expression still makes it None. Longer than
    :data:`LICENSE_WORDS_MAX` characters is None (no real licence field is).
    """
    if not isinstance(spdx_or_family, str) or not spdx_or_family.strip():
        return None
    text = " ".join(spdx_or_family.split())
    if len(text) > LICENSE_WORDS_MAX:
        return None
    fam = _family(text)                          # a family name with spaces ("CC BY-SA 4.0")
    if fam is not None:
        return _WORDS[fam].format(id=text)
    ids = _expression(text)
    if not ids:
        return None
    fams = [_family(p) for p in ids]
    if any(f is None for f in fams):
        return None
    worst = max(fams, key=lambda f: _RANK[f])
    if text.startswith("(") and text.endswith(")") and _expression(text[1:-1]):
        text = text[1:-1]                        # one wrapping pair adds nothing to read
    return _WORDS[worst].format(id=text)


#: Licences whose mere naming makes a text unusable, whatever else it says.
_RESTRICTIVE_NAMES: tuple[tuple[str, str], ...] = (
    (r" commons clause ", "Commons-Clause"),
    (r" business source licen[sc]e ", "BUSL-1.1"),
)
#: Linear on any text: a web address starts only at "http" or "www.".
_URL_RE = re.compile(r"<?(?:https?://|www\.)[^\s<>]+>?", re.I)
#: The sites that publish the licences themselves. An address on one of them
#: says nothing about the terms and is left out of the words read. Any other
#: web address, and every e-mail address, is read as its words, like the rest
#: of the text: "https://example.com/no-commercial-use" after a licence reads
#: "no commercial use" and makes the file custom (dropping every address unread
#: would hide a restriction written as an address, or inside one; dropping
#: e-mail addresses also takes minutes on a long word with no "@").
_LICENSE_SITES = ("apache.org", "opensource.org", "gnu.org", "fsf.org", "creativecommons.org",
                  "mozilla.org", "unlicense.org", "spdx.org", "sil.org", "openfontlicense.org",
                  "wtfpl.net", "choosealicense.com")


#: The path words a licence publisher's address may have and still say nothing
#: about the terms ("https://www.gnu.org/licenses/why-not-lgpl.html",
#: "http://www.apache.org/licenses/LICENSE-2.0", ".../publicdomain/zero/1.0/").
#: Not "nc" or "nd": an address naming a non-commercial or no-changes licence
#: is a term (otherwise "https://creativecommons.org/licenses/by-nc/4.0/"
#: after an MIT text is dropped unread and the file reads as MIT).
_LICENCE_PATH_WORDS = frozenset("""
licenses license licence licences by sa publicdomain zero deed en legalcode mpl ofl philosophy
why not lgpl gpl agpl html htm txt md mit apache isc bsd policies old faq fdl
""".split())


def _licence_site(address: str) -> bool:
    """An address on a licence publisher's site whose path only points at a
    licence (plain ASCII, no query or fragment, known path words and numbers)."""
    text = address.strip("<>").rstrip(".,;:")
    if not text.isascii():
        return False
    if not re.match(r"^https?://", text, re.I):
        text = "https://" + text
    try:
        split = urllib.parse.urlsplit(text)
        host = (split.hostname or "").lower()
    except ValueError:
        return False
    if not any(host == d or host.endswith("." + d) for d in _LICENSE_SITES):
        return False
    if split.query or split.fragment or split.username or split.password:
        return False
    # An address with its own port is not the
    # publisher's page ("opensource.org:8080/licenses/mit"), and "why" and
    # "not" are path words only in GNU's own essay ("why-not-lgpl"), never
    # elsewhere ("why-not-mit").
    if re.search(r":\d*$", split.netloc):
        return False
    pieces = [p for p in re.split(r"[^a-z0-9]+", split.path.lower()) if p]
    if ("why" in pieces or "not" in pieces) and \
            not re.fullmatch(r"/(?:licenses|philosophy)/why-not-lgpl\.html?", split.path.lower()):
        return False
    return all(p.isdigit() and len(p) <= 3 or p in _LICENCE_PATH_WORDS for p in pieces)


def _drop_licence_sites(low: str) -> str:
    return _URL_RE.sub(lambda m: " " if _licence_site(m.group(0)) else m.group(0), low)


#: Words that, outside a licence's own sentences, could add a condition or take
#: a permission away ("shall not be used for commercial purposes", "for Good,
#: not Evil", "provided that you pay a fee"). None may appear in a gap between
#: the sentences, in the title and copyright part before them, or after them.
_RESTRICT_WORDS = frozenset({
    "not", "no", "non", "never", "neither", "cannot", "without", "except", "excluding", "unless",
    "only", "solely", "exclusively", "commercial", "commercially", "noncommercial",
    "prohibited", "prohibit", "prohibits", "forbidden", "forbid", "forbids", "banned",
    "restriction", "restrictions", "restricted", "restrict", "limited", "limit",
    "fee", "fees", "pay", "paid", "payment", "royalty", "royalties", "charge", "price",
    "sell", "sale", "resale", "resell", "evil", "harm", "military", "personal", "private",
    "educational", "academic", "shall", "must", "may", "should", "provided", "condition",
    "conditions", "terms", "clause", "permission", "permitted", "allowed", "allow",
    "use", "used", "using", "agree", "agreement", "license", "licence", "licensed",
    "licenses", "licences", "licensee", "revoke", "revoked", "terminate", "void",
    "noderivatives", "noderivs", "sharealike", "attribution", "unlicensed", "proprietary",
    "confidential", "donation", "donate", "subscription",
})
#: A title before the licence's own sentences may name it ("MIT License",
#: "BSD 2-Clause License"); the Commons Clause is caught before any title is read.
_TITLE_WORDS = frozenset({"license", "licence", "licensed", "licenses", "licences", "clause"})
_PREFIX_MAX_WORDS = 60
_TAIL_MAX_WORDS = 30
_NAME_GAP = 16          # a copyright holder's name inside a sentence
_SMALL_GAP = 3          # numbering and bullets between clauses


#: What a letter, digit or symbol no licence BuildBot knows is written with
#: becomes in the words read: one word that matches nothing, so it can never
#: vanish (otherwise a sentence in Cyrillic, Greek, Chinese or any other
#: script, or an emoji, is dropped like a space, so "商用利用禁止" after
#: an MIT text, or inside it, still reads as MIT).
_FOREIGN_WORD = "\x01"
#: ASCII control characters that are not spaces: never in a licence.
_CTRL_RE = re.compile(r"[\x00-\x08\x0e-\x1b\x7f]")
#: Symbols a licence or a copyright line may carry; they read as a space.
_KEEP_SYMBOLS = frozenset("©®°")
#: Latin letters that do not come apart into a plain letter and a mark
#: ("Łukasz", "Søren", "Weiß"), and look-alikes of plain letters from the
#: same alphabet ("Corporatıons" with a dotless i). Each reads as the plain
#: letter, so a word is the word it looks like.
_LETTER_MAP = {
    "ł": "l", "Ł": "L", "ø": "o", "Ø": "O", "ß": "ss", "ẞ": "SS", "æ": "ae", "Æ": "AE",
    "œ": "oe", "Œ": "OE", "đ": "d", "Đ": "D", "ı": "i", "þ": "th", "Þ": "Th", "ð": "d",
    "Ð": "D", "ħ": "h", "Ħ": "H", "ŀ": "l", "Ŀ": "L", "ŧ": "t", "Ŧ": "T", "ĸ": "k",
    "ƀ": "b", "Ɓ": "B", "ƈ": "c", "Ƈ": "C", "ɗ": "d", "Ɗ": "D", "ƒ": "f", "Ƒ": "F",
    "ɠ": "g", "Ɠ": "G", "ɨ": "i", "Ɨ": "I", "ƙ": "k", "Ƙ": "K", "ƚ": "l", "Ƚ": "L",
    "ɲ": "n", "Ɲ": "N", "ƞ": "n", "Ƞ": "N", "ɵ": "o", "Ɵ": "O", "ƥ": "p", "Ƥ": "P",
    "ƫ": "t", "ƭ": "t", "Ƭ": "T", "ʈ": "t", "Ʈ": "T", "ʋ": "v", "Ʋ": "V", "ƴ": "y",
    "Ƴ": "Y", "ƶ": "z", "Ƶ": "Z", "ǝ": "e", "Ǝ": "E", "ə": "e", "Ə": "E", "ɛ": "e",
    "Ɛ": "E", "ɑ": "a", "ɐ": "a", "ɡ": "g", "ɩ": "i", "ɔ": "o", "Ɔ": "O", "ʃ": "s",
    "ʒ": "z", "Ʒ": "Z", "ŋ": "n", "Ŋ": "N", "ĳ": "ij", "Ĳ": "IJ", "ŉ": "n", "ſ": "s",
}


def _fold_char(ch: str) -> str:
    """One character of NFKD text, read as a licence reads it: a plain letter
    or digit, a space, nothing (a mark or an invisible format character), or
    :data:`_FOREIGN_WORD`."""
    import unicodedata
    if ch < "\x80":
        return _FOREIGN_WORD if _CTRL_RE.match(ch) else ch
    if ch in _LETTER_MAP:
        return _LETTER_MAP[ch].lower()
    cat = unicodedata.category(ch)
    if cat in ("Mn", "Cf"):
        return ""                              # an accent, a zero-width space, a BOM
    if ch.isspace() or cat[0] in "PZ" or cat in ("Sm", "Sk") or ch in _KEEP_SYMBOLS:
        return " "
    return f" {_FOREIGN_WORD} "               # a letter, digit or symbol of another kind


def _mark_foreign(low: str) -> str:
    import unicodedata
    return "".join(_fold_char(c) for c in unicodedata.normalize("NFKD", low).lower())


def _lic_norm(text: str) -> str:
    low = text.lower()
    for a, b in (("’", "'"), ("‘", "'"), ("“", '"'), ("”", '"')):
        low = low.replace(a, b)
    low = _drop_licence_sites(low)
    if not low.isascii() or _CTRL_RE.search(low):
        low = _mark_foreign(low)
    return " " + " ".join(re.sub(r"[^a-z0-9\x01]+", " ", low).split()) + " "


def _t(gap: Optional[int], sentence: str) -> tuple[Optional[int], str]:
    return gap, _lic_norm(sentence)


_MIT = (
    _t(_PREFIX_MAX_WORDS, "Permission is hereby granted, free of charge, to any person obtaining a "
       "copy of this software and associated documentation files"),
    _t(_SMALL_GAP, "to deal in the Software without restriction, including without limitation "
       "the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell "
       "copies of the Software, and to permit persons to whom the Software is furnished to do "
       "so, subject to the following conditions:"),
    _t(0, "The above copyright notice and this permission notice shall be included in all "
       "copies or substantial portions of the Software."),
    _t(0, "THE SOFTWARE IS PROVIDED AS IS, WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, "
       "INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A "
       "PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE"),
    _t(_NAME_GAP, "BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF "
       "CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR "
       "THE USE OR OTHER DEALINGS IN THE SOFTWARE."),
)
_ISC_GRANT = ("Permission to use, copy, modify, and/or distribute this software for any purpose "
              "with or without fee is hereby granted")
_ISC_DISCLAIMER = (
    _t(_NAME_GAP, "THE SOFTWARE IS PROVIDED AS IS AND"),
    _t(_NAME_GAP, "DISCLAIMS ALL WARRANTIES WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED "
       "WARRANTIES OF MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL"),
    _t(_NAME_GAP, "BE LIABLE FOR ANY SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY "
       "DAMAGES WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN AN ACTION OF "
       "CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF OR IN CONNECTION WITH THE "
       "USE OR PERFORMANCE OF THIS SOFTWARE."),
)
_ISC = (_t(_PREFIX_MAX_WORDS, _ISC_GRANT + ", provided that the above copyright notice and this "
           "permission notice appear in all copies."),) + _ISC_DISCLAIMER
_0BSD = (_t(_PREFIX_MAX_WORDS, _ISC_GRANT + "."),) + _ISC_DISCLAIMER
_BSD_HEAD = (
    _t(_PREFIX_MAX_WORDS, "Redistribution and use in source and binary forms, with or without "
       "modification, are permitted provided that the following conditions are met:"),
    _t(_SMALL_GAP, "Redistributions of source code must retain the above copyright notice, this "
       "list of conditions and the following disclaimer."),
    _t(_SMALL_GAP, "Redistributions in binary form must reproduce the above copyright notice, "
       "this list of conditions and the following disclaimer in the documentation and/or other "
       "materials provided with the distribution."),
)
_BSD_CLAUSE_3 = (
    _t(_SMALL_GAP, "Neither the"),          # "name of X nor the names of its contributors"
    _t(_NAME_GAP, "may be used to endorse or promote products derived from this software "
       "without specific prior written permission."),
)
_BSD_TAIL = (
    _t(_SMALL_GAP, "THIS SOFTWARE IS PROVIDED BY"),
    _t(_NAME_GAP, "AS IS AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, "
       "THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE "
       "DISCLAIMED. IN NO EVENT SHALL"),
    _t(_NAME_GAP, "BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR "
       "CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR "
       "SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON "
       "ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING "
       "NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF "
       "ADVISED OF THE POSSIBILITY OF SUCH DAMAGE."),
)
_UNLICENSE = (
    _t(_PREFIX_MAX_WORDS, "This is free and unencumbered software released into the public "
       "domain."),
    _t(0, "Anyone is free to copy, modify, publish, use, compile, sell, or distribute this "
       "software, either in source code form or as a compiled binary, for any purpose, "
       "commercial or non-commercial, and by any means."),
    _t(0, "In jurisdictions that recognize copyright laws, the author or authors of this "
       "software dedicate any and all copyright interest in the software to the public domain. "
       "We make this dedication for the benefit of the public at large and to the detriment of "
       "our heirs and successors. We intend this dedication to be an overt act of "
       "relinquishment in perpetuity of all present and future rights to this software under "
       "copyright law."),
    _t(0, "THE SOFTWARE IS PROVIDED AS IS, WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, "
       "INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A "
       "PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS BE LIABLE FOR ANY "
       "CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, "
       "ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN "
       "THE SOFTWARE."),
)
_ZLIB = (
    _t(_PREFIX_MAX_WORDS, "This software is provided 'as-is', without any express or implied "
       "warranty. In no event will the authors be held liable for any damages arising from the "
       "use of this software."),
    _t(0, "Permission is granted to anyone to use this software for any purpose, including "
       "commercial applications, and to alter it and redistribute it freely, subject to the "
       "following restrictions:"),
    _t(_SMALL_GAP, "The origin of this software must not be misrepresented; you must not claim "
       "that you wrote the original software. If you use this software in a product, an "
       "acknowledgment in the product documentation would be appreciated but is not required."),
    _t(_SMALL_GAP, "Altered source versions must be plainly marked as such, and must not be "
       "misrepresented as being the original software."),
    _t(_SMALL_GAP, "This notice may not be removed or altered from any source distribution."),
)
_WTFPL = (
    _t(_PREFIX_MAX_WORDS, "Everyone is permitted to copy and distribute verbatim or modified "
       "copies of this license document, and changing it is allowed as long as the name is "
       "changed."),
    _t(0, "DO WHAT THE FUCK YOU WANT TO PUBLIC LICENSE TERMS AND CONDITIONS FOR COPYING, "
       "DISTRIBUTION AND MODIFICATION"),
    _t(_SMALL_GAP, "You just DO WHAT THE FUCK YOU WANT TO."),
)
_APACHE_NOTICE = (
    _t(_PREFIX_MAX_WORDS, 'Licensed under the Apache License, Version 2.0 (the "License");'),
    _t(0, "you may not use this file except in compliance with the License."),
    _t(0, "You may obtain a copy of the License at"),
    _t(0, "Unless required by applicable law or agreed to in writing, software distributed "
       "under the License is distributed on an AS IS BASIS, WITHOUT WARRANTIES OR CONDITIONS OF "
       "ANY KIND, either express or implied."),
    _t(0, "See the License for the specific language governing permissions and limitations "
       "under the License."),
)
#: The short licences: (id, sentences in order, optional closing part). Tried
#: in this order; the first whose every sentence is found, with clean gaps and
#: a clean start and end, names the licence. The long licences are read word
#: by word from the ``licence_words`` part of ``config/where_to_look.json``
#: (:func:`_long_editions`).
#: Anything else reads as None (not usable).
_LICENSE_TEMPLATES: tuple[tuple[str, tuple, tuple], ...] = (
    ("MIT", _MIT, ()),
    ("ISC", _ISC, ()),
    ("0BSD", _0BSD, ()),
    ("BSD-3-Clause", _BSD_HEAD + _BSD_CLAUSE_3 + _BSD_TAIL, ()),
    ("BSD-2-Clause", _BSD_HEAD + _BSD_TAIL, ()),
    ("Unlicense", _UNLICENSE, (_t(_SMALL_GAP, "For more information, please refer to"),)),
    ("Zlib", _ZLIB, ()),
    ("WTFPL", _WTFPL, ()),
    ("Apache-2.0", _APACHE_NOTICE, ()),
)
_LICENSE_TEXT_MAX = 200_000
#: What a licence file that was read, and is not a licence BuildBot knows,
#: is recorded as (an SPDX "LicenseRef-" id, so no reader takes it for empty
#: and no reported licence stands in for it). Never usable.
CUSTOM_LICENSE = "LicenseRef-custom"
#: What a licence is recorded as when it is there (reported, or a usual
#: licence file was found) but could not be confirmed, because the listing of
#: the project's top files could not be had: GitHub's keyless allowance for
#: this build was used up. Never usable (fail closed), like
#: :data:`CUSTOM_LICENSE`, but the plan says why plainly instead of "its
#: licence has terms of its own" (otherwise an LGPL-3.0 project read file by
#: file after the allowance ran out is shown as custom).
UNCHECKED_LICENSE = "LicenseRef-not-checked"
#: A licence file with more lines than this is not a licence (the longest real one has ~700).
_LICENSE_LINES_MAX = 5_000
#: At most this many title/copyright lines are read before or after the licence.
_EDGE_LINES_MAX = 60


# The long licences, word by word -------------------------------------------
#
# A long licence is recognised only when every one of its words is there, in
# order (a reader that looks for a few anchor sentences and skips what
# stands between them reads "You may not use the Work for commercial
# purposes." in the middle of the Apache licence as Apache-2.0). Real copies differ in a few known places only; the file
# lists them as exact alternatives. The one place that is not a fixed text,
# the owner line of the Apache appendix, takes only whole lines that
# :func:`_is_copyright_line` accepts. No free words are allowed anywhere.

#: The key in ``config/where_to_look.json`` that holds the long licences'
#: words (kept in the registry file this module owns, not a file of its own).
LICENCE_WORDS_KEY = "licence_words"
LICENCE_WORDS_SCHEMA = "puffpai_licence_words/v1"
#: The ids a long edition may name: every one is known to :func:`_family`.
_LONG_IDS = frozenset({"Apache-2.0", "MPL-2.0", "AGPL-3.0", "LGPL-3.0", "GPL-3.0", "LGPL-2.1",
                       "GPL-2.0", "CC0-1.0", "OFL-1.1", "CC-BY-4.0", "CC-BY-SA-4.0",
                       "CC-BY-NC-4.0", "CC-BY-NC-SA-4.0", "CC-BY-NC-ND-4.0", "CC-BY-ND-4.0"})
_LONG_WORDS_RE = re.compile(r"^[a-z0-9]+(?: [a-z0-9]+)*$")
_LONG_CLINE_MAX = 3
_LONG_DEPTH_MAX = 3
_LONG_LOCK = threading.Lock()
_LONG_CACHE: dict[str, Any] = {}


def _long_elements(raw: Any, depth: int) -> tuple:
    """One element list from the file, checked; ValueError on any other shape."""
    if not isinstance(raw, list) or not raw or depth > _LONG_DEPTH_MAX:
        raise ValueError("an element list")
    out = []
    for el in raw:
        if not isinstance(el, dict) or len(el) != 1:
            raise ValueError("an element")
        (kind, value), = el.items()
        if kind == "w":
            if not isinstance(value, str) or not _LONG_WORDS_RE.match(value):
                raise ValueError("words")
            out.append(("w", value.split()))
        elif kind == "alt":
            if not isinstance(value, list) or len(value) < 2:
                raise ValueError("alternatives")
            choices = tuple(() if c == [] else _long_elements(c, depth + 1) for c in value)
            out.append(("alt", choices))
        elif kind == "opt":
            out.append(("opt", _long_elements(value, depth + 1)))
        elif kind == "cline":
            if not isinstance(value, int) or isinstance(value, bool)                     or not 1 <= value <= _LONG_CLINE_MAX:
                raise ValueError("copyright lines")
            out.append(("cline", value))
        else:
            raise ValueError("an element kind")
    return tuple(out)


def load_long_editions(path: Optional[str] = None) -> list[tuple[str, tuple]]:
    """``[(spdx, elements)]`` from *path* (default: the ``licence_words`` part
    of ``config/where_to_look.json``). *path* may also be a file holding only
    a ``puffpai_licence_words/v1`` document.

    Fail closed: a missing, unreadable or wrongly shaped file gives ``[]``,
    and then no long licence is recognised (it reads as None, not usable).
    """
    path = path or registry_path()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict) and data.get("schema") == SCHEMA:
            data = data.get(LICENCE_WORDS_KEY)
        if not isinstance(data, dict) or data.get("schema") != LICENCE_WORDS_SCHEMA:
            return []
        out = []
        for ed in data.get("editions") or []:
            if not isinstance(ed, dict) or ed.get("id") not in _LONG_IDS:
                return []
            out.append((ed["id"], _c_optional(_long_elements(ed.get("elements"), 0))))
        return out
    except (OSError, ValueError, RecursionError, TypeError):
        return []


def _c_optional(elements: tuple) -> tuple:
    """Every "copyright c" of a long licence also reads as "copyright" alone.

    Without this a real licence reads as custom: the
    Markdown copies of the GNU licences write "Copyright © 2007" where the
    plain text has "Copyright (C) 2007", and "©" reads as a space, so the
    very first words did not match (msmhq/msm's GPL-3.0). Only the one
    letter "c" may be left out, right after "copyright": no word can be
    added, so no term can come in this way.
    """
    out: list = []
    for kind, value in elements:
        if kind == "w":
            start = 0
            for i in range(len(value) - 1):
                if value[i] == "copyright" and value[i + 1] == "c":
                    out.append(("w", value[start:i + 1]))
                    out.append(("opt", (("w", ["c"]),)))
                    start = i + 2
            if value[start:]:
                out.append(("w", value[start:]))
        elif kind == "alt":
            out.append(("alt", tuple(_c_optional(c) if c else c for c in value)))
        elif kind == "opt":
            out.append(("opt", _c_optional(value)))
        else:
            out.append((kind, value))
    return tuple(out)


def _long_editions() -> list[tuple[str, tuple]]:
    with _LONG_LOCK:
        if "editions" not in _LONG_CACHE:
            _LONG_CACHE["editions"] = load_long_editions()
        return _LONG_CACHE["editions"]


class _LongText:
    """One licence file as words, with where each line's words start."""

    def __init__(self, lines: list[str], words: list[list[str]], flat: list[str],
                 starts: list[int], *, ident: tuple = (), holders: tuple = ()):
        self.lines, self.flat, self.starts = lines, flat, starts
        self.ident, self.holders = ident or _NO_ID, list(holders)
        # The first word index of every line that has words -> that line.
        self.line_at = {starts[k]: k for k in range(len(lines)) if words[k]}

    def ends(self, i: int, elements: tuple) -> set[int]:
        """Every place where *elements*, read from word *i*, can end."""
        now = {i}
        for el in elements:
            nxt: set[int] = set()
            for at in now:
                nxt |= self._one(at, el)
            now = nxt
            if not now:
                break
        return now

    def _one(self, i: int, el: tuple) -> set[int]:
        kind, value = el
        if kind == "w":
            n = len(value)
            if i + n > len(self.flat) or self.flat[i] != value[0] or self.flat[i:i + n] != value:
                return set()
            return {i + n}
        if kind == "alt":
            out: set[int] = set()
            for choice in value:
                out |= self.ends(i, choice) if choice else {i}
            return out
        if kind == "opt":
            return {i} | self.ends(i, value)
        # "cline": one to *value* whole lines, each a copyright line on its
        # own, their holders read together with the edge lines' holders.
        out, at = set(), i
        named = list(self.holders)
        for _ in range(value):
            k = self.line_at.get(at)
            if k is None or _line_kind(self.lines[k], self.ident) != "copyright":
                break
            named.extend(_copyright_holders(_plain_line(self.lines[k]), self.ident) or ())
            if not _holders_ok(named, self.ident):
                break
            at = self.starts[k + 1]
            out.add(at)
        return out


def _clean(words: list[str], *, title: bool = False) -> bool:
    banned = _RESTRICT_WORDS - _TITLE_WORDS if title else _RESTRICT_WORDS
    # "commerc" inside any word too ("nocommercial", "noncommercially"); a
    # word in a script no licence is written in is never clean.
    return not any(w in banned or "commerc" in w or _FOREIGN_WORD in w for w in words)


#: The only words a gap between a short licence's sentences may hold:
#: numbering ("1.", "(a)", "iii.") and the "(the "Software")" that MIT names
#: its subject with. Any other word, in any language, is an added term (read
#: against a list of English restricting words, "(Unternehmen verboten)" or
#: "(imprese escluse)" in place of "(the "Software")" would read as MIT).
_GAP_WORDS = ("the", "software")
_ROMAN = re.compile(r"^(?:i{1,3}|iv|v|vi{1,3}|ix|x)$")


def _gap_ok(words: list[str]) -> bool:
    """Nothing, MIT's "the software" as written, or ONE numbering mark ("1",
    "a", "iii"). Never any run of single letters or numbers ("(n c)",
    "(x y z)" in place of (the "Software"))."""
    if not words or tuple(words) == _GAP_WORDS:
        return True
    if len(words) != 1:
        return False
    w = words[0]
    return (w.isdigit() and len(w) <= 2) or (len(w) == 1 and w.isalpha()) or bool(_ROMAN.match(w))


#: The words the short licences themselves put where a holder is named ("THE
#: AUTHORS OR COPYRIGHT HOLDERS", "the name of the copyright holder nor the
#: names of its contributors", "THE REGENTS", "THE X CONSORTIUM").
_HOLDER_WORDS = frozenset("""
the authors author or and copyright holder holders contributors contributor owner owners
name names of nor its x consortium regents university california organization organisation
isc internet systems
""".split())


def _follow(norm: str, pos: int, sentences, *, first_is_title: bool,
            names: frozenset = frozenset()) -> Optional[int]:
    """Find *sentences* in order from *pos*; the end position, or None.

    The gap before each sentence must be at most its limit in words and hold
    no restricting word (a limit of None is read as 0: no gap is ever left
    unread). A gap where a holder is named (:data:`_NAME_GAP`) must be whole
    names only: the licences' own holder words and whole holder names from
    the file's own copyright lines (*names*, word tuples; see
    :func:`_names_fill`). A loose word from a copyright line is not enough
    ("COMPANIES WITH MORE THAN TEN EMPLOYEES NEED TO BUY A SEAT", or "JANE
    DOE EMPLOYEES BUY SEAT", in MIT's "SHALL THE ... BE LIABLE" must not read
    as MIT). When *first_is_title*, the first
    gap is the title and copyright part (a title may say "License").
    """
    for i, (gap, sentence) in enumerate(sentences):
        at = norm.find(sentence, pos)
        if at < 0:
            return None
        between = norm[pos:at].split()
        if len(between) > (gap or 0):
            return None
        if gap == _NAME_GAP:
            # Whole names only. A name was read by _copyright_holders (shape and
            # condition words), so "BRIAN MAY" here is a name, not "may".
            if not _names_fill(between, names):
                return None
        elif first_is_title and i == 0:
            if not _clean(between, title=True):   # no caller reads a title here today
                return None
        elif not _gap_ok(between):
            return None
        pos = at + len(sentence) - 1          # keep the trailing space for the next find
    return pos


#: Words a title line before a short licence may have ("MIT License", "BSD
#: 3-Clause License", "DO WHAT THE ... PUBLIC LICENSE Version 2, December 2004").
_TITLE_LINE_WORDS = frozenset("""
the mit isc bsd 0bsd zero clause new simplified revised modified original apache version v zlib
libpng unlicense wtfpl license licence expat x11 open source software do what fuck you want to
public january february march april may june july august september october november december
""".split())
#: Words that make a line a title (one of them must be there).
_TITLE_LINE_NEEDS = frozenset({"license", "licence", "unlicense", "wtfpl"})
_COPYRIGHT_HEAD = re.compile(r"^(?:copyright\b\s*(?:\(c\)|©)?|\(c\)|©)\s*", re.I)
#: Years, ranges and open ranges ("2019-2024", "2015-present", "2016-,", "2017 - Name").
_YEARS = re.compile(r"^(?:\s*\d{4}(?:\s*[-–]\s*(?:\d{4}|present|now|\*)?)?\s*,?)+", re.I)
_RIGHTS = re.compile(r"[\s,.]*all rights reserved\.?\s*$", re.I)
#: What a copyright holder's name may be written with: Latin letters (with
#: accents), digits, spaces and the marks names, addresses and e-mails use.
#: No ";", "!", "?", quotes or other scripts (a look-alike letter from another
#: alphabet is how a hidden sentence would slip in).
_HOLDER_CHARS = re.compile(r"^[A-Za-z0-9À-ÖØ-öø-ɏ .,'&()<>@:/_+\-]*$")
_ABBREVIATIONS = frozenset("inc ltd co corp llc gmbh jr sr st pty plc ag sa bv ab oy srl kk "
                           "e.v s.a b.v n.v".split())
_SPDX_LINE = re.compile(r"^spdx-license-identifier:\s*([A-Za-z0-9.+-]{1,60})$", re.I)
_URL_LINE = re.compile(r"^<?(?:https?://|www\.)[^\s<>]+>?$", re.I)
_DECORATION_CHARS = frozenset("#>*=_|`~-")
#: A title, copyright, address or SPDX line is short. A longer line is "other"
#: before any pattern reads it, so no line can make the edge checks slow.
_EDGE_LINE_MAX_CHARS = 400


def _strip_decoration(text: str) -> str:
    """Spaces and ``# > * = _ | ` ~ -`` off both ends, in one linear pass."""
    i, j = 0, len(text)
    while i < j and (text[i].isspace() or text[i] in _DECORATION_CHARS):
        i += 1
    while j > i and (text[j - 1].isspace() or text[j - 1] in _DECORATION_CHARS):
        j -= 1
    return text[i:j]


def _plain_line(line: str) -> str:
    return _strip_decoration(unicodedata_normalize(line))


def unicodedata_normalize(text: str) -> str:
    import unicodedata
    return unicodedata.normalize("NFKC", text)


def _is_title_line(line: str) -> bool:
    words = _lic_norm(line).split()
    return (0 < len(words) <= 12
            and (any(w in _TITLE_LINE_NEEDS for w in words) or words[0] == "version")
            and all(w in _TITLE_LINE_WORDS or w.isdigit() for w in words))


_NAME_TOKEN = re.compile(r"^[A-Z\u00c0-\u00de][A-Za-z\u00c0-\u024f'-]*$")


def _is_names_line(line: str) -> bool:
    """Two or more capitalised names on one line ("Jean-loup Gailly   Mark Adler")."""
    if len(line) > _EDGE_LINE_MAX_CHARS:
        return False
    parts = [p for p in re.split(r"\s{2,}|\s+and\s+|,\s*", _plain_line(line)) if p.strip()]
    if len(parts) < 2 or sum(len(p.split()) for p in parts) > 10:
        return False
    return _condition_free(line) and all(
        2 <= len(p.split()) <= 4 and all(_NAME_TOKEN.match(t) for t in p.split())
        and _holder_name(p) is not None for p in parts)


def _is_copyright_line(line: str, ident: tuple = ()) -> bool:
    """``Copyright (c) 2019-2024 Holder Name <mail>. All rights reserved.``: only
    names, and holders that may stand next to a licence (:func:`_holders_ok`)."""
    ident = ident or _NO_ID
    holders = _copyright_holders(line, ident)
    return holders is not None and _holders_ok(holders, ident)


# Holder names -----------------------------------------------------------------
#
# A copyright line may name holders and nothing else (looking only for the
# English restricting words, "Copyright (c) 2024 Jane Doe, kommerzielle
# Nutzung verboten" and "..., companies with employees buy a seat" would read
# as MIT). Two checks, both fail closed:
#
# * the shape of a name: each holder is capitalised words, the small words
#   names carry ("van", "de", "the ... authors"), at most one lowercase handle
#   ("sindresorhus"), and web or e-mail addresses. A clause in lowercase words,
#   in any language, is not a name;
# * no condition word, read with accents folded ("gebühr" as "gebuhr"), in
#   English and the common European languages, anywhere in the holder part or
#   its addresses. This catches a condition written in capitalised words
#   ("Businesses Buy Seats"). It is a list, so it cannot be complete: a
#   capitalised condition in words it does not know still reads as a name.

#: Parts of a word that make it a condition word wherever they stand ("noncommercial").
_CONDITION_PARTS = ("commerc", "comerc", "kommerz", "kommers", "komerc", "licen", "lizen",
                    "keresked", "kaupall",
                    # "no trade", "for profit", "sell",
                    # "forbidden" and "personal use" in languages written in other
                    # scripts, in Latin letters, and the "-ware" names of licences
                    # that are not open. Looked for inside every word, any case
                    # ("Shougyou-Kinshi", "JinzhiShangyong", "Nekommercheskoe").
                    # None is a part of a given name or surname we know of.
                    "jinzhi", "shangyong", "shangye", "kinshi", "shougyou", "shougyo",
                    "shogyo", "eigyou", "eigyo", "hieiri", "hanbai", "geumji", "sangeop",
                    "faqat", "shaxsiy", "tijorat", "tijari", "taqiq", "mamnu", "gebrauch",
                    "kaytto", "kielto", "zapresh", "zapresch", "kommerch", "vyavsay",
                    "vyavasay", "nishiddh", "nishedh", "shareware", "freeware", "trialware",
                    "demoware", "nagware", "crippleware", "kojinriyo", "riyoukinshi", "prodej",
                    "prodaj", "prodav", "sprzeda", "zhuanyong", "jeonyong", "varjit", "kummerc",
                    "mischar", "yeongri", "senyou", "pambenta", "binafsi", "matumizi",
                    "peribadi", "pansarili", "osobyst", "kaufmannisch", "negoce", "lucro",
                    "nyumbani", "upyog", "vyapar", "komersyo", "komers")
#: Starts of words that make them condition words.
_CONDITION_STARTS = (
    "business", "enterpris", "employ", "purchas", "subscri", "prohib", "proib", "forbid",
    "forbud", "forbj", "verbot", "verbod", "interdi", "vieta", "zabron", "zakaz", "kiell",
    "tiltot", "yasak", "untersag", "restrict", "exclusiv", "personal", "privat", "educational",
    "academic", "evaluat", "payment", "revenue", "royalt", "gewerb", "unternehm", "entrepris",
    "empresa", "aziend", "bedrijf", "foretag", "betal", "bezahl", "gebuhr", "kostenpfl",
    "achat", "achet", "pagam", "pagar", "nutzung", "benutz", "permis", "permit", "erlaub",
    "autoris", "authoris", "authoriz", "allow", "sponsor", "militar", "weapon", "noncom",
    "nonprof", "customer", "client", "internal", "condition", "bedingung", "requir", "obligat",
    "mandat", "gratuit", "exclud",
    # who is shut out, and "shut out", in more languages
    "exclu", "esclu", "ausgeschl", "ausschl", "uitgeslot", "utesl", "udeluk", "wyklucz",
    "vylouc", "vylouch", "kizar", "dikecual", "bedrijv", "yrity", "virksomh", "sirket",
    "perusahaan", "gewinn", "lucrat", "profit", "remunera", "invoic", "disallow", "govern",
    "gouvern", "gobiern", "regierung", "overheid", "abonn", "fizet", "ucret", "zahlung",
    "militaer", "armee", "verbod", "vietat", "prohibid", "interdit", "zakazan", "barred",
    # more of the same list (not the closure: a
    # condition in words no list knows stays the residual): company,
    # business, commercial and forbidden in more languages
    "ticar", "kompani", "compani", "zabran", "interzi", "marufuk", "dilarang", "negoti",
    "negosy", "kibiashar", "biashar", "kumpan", "syarikat", "prepoved", "keelat", "aizlieg",
    "malpermes", "vetit",
)
#: Whole words that are condition words (short ones, which a start would over-match).
_CONDITION_WORDS = frozenset("""
buy buys bought buyer buyers pay pays paid payer fee fees seat seats sale sales sell sells sold
cost costs prices pricing only not no non never must shall should need needs contact unless
without except free gratis trial use uses usage used user users uso companies evil students
hobby hobbies hobbyist hobbyists donate donation donations neither cannot solely banned resale
resell provided terms agree agreement revoke revoked terminate void noderivatives noderivs
sharealike attribution proprietary confidential
kauf kaufen kauft gekauft vendre vendu vender venda venta vendere comprar compra acheter
nur nicht kein keine keinen pas ne seulement uniquement sauf solo solamente somente apenas
alleen niet tylko nie endast bara inte kun ikke vain ei pouze csak nem samo tolko sadece hanya
firmas firme firmen firmer firmor firmy firms corporations corps megacorps imprese
societes sociedades aziende bolagen selskaper spolki
cegek empresas companhias compagnies ingen aucun aucune ningun ninguna ninguno nessun nessuna
nessuno geen zadne zadna zadny zaden tidak bukan excepted denied banned
individuals particulier particuliers particulares privati hobbyist charities students
studenten etudiants estudiantes studenti police polizei policia payant payer paye zahlen
pagare oplata winst vinst yalniz uitsluitend
firm bez hakuna bawal kampuni tilos bannad
owe owes owed owing
""".split())
#: Restriction words written in Latin letters for
#: another language that are also given names, surnames or everyday words
#: ("Riyo", "Saja", "Mai", "Ban", "Hindi"). One beside a real name is a name
#: ("Kojin Tanaka", "Saja Ahmed"); a holder whose every name word is one of
#: these or a condition word is a phrase ("Kojin Riyou": personal use, "Rak
#: Ishi": only personal, "Kewal Niji", "Khong Buon Ban", "Hobi Saja").
_WEAK_CONDITION_WORDS = frozenset("""
riyo riyou kojin eiri ishi rak katei senyo ghar saja rumah hobi khong buon ban gamit mai kewal
niji guna kinh doanh cam thuong tu hi lo bi sem sin senza sans tolko lichno gair ghayr bila
kinyume walang hindi bukan niaga ham bey khai jiating gajeong nein ohne tylko solo uso
""".split())
#: Words a name never has, in any case: pronouns and the small words that
#: start a clause ("Jane Doe, If You Ship It").
_NOT_NAME_WORDS = frozenset("""
i we our ours us they them their it is are be been was were please before after if when
whenever unless any every each anyone everyone anybody nobody to with from via per than then
""".split())
#: More words a name never has when written in lowercase ("Will Smith" is a
#: name; "will" in lowercase is a verb).
_NOT_NAME_LOWER = frozenset("""
you your yours he she his her him does did has have had can could will would may might want
wants see read visit go ask asks who whom which what why how where while at on by into onto
upon all something nothing someone ok yes
""".split())
#: The small words a holder's name may carry in lowercase.
_NAME_PARTICLES = frozenset("""
the of and van von der den de da di du del della des la le dos das do bin ibn al el y zu af av
ten ter et al contributors contributor authors author developers developer project team
community others individual maintainers its affiliates affiliate subsidiaries
""".split())
#: A company word that may follow a name after a comma ("Fair Oaks Labs, Inc.").
_COMPANY_WORDS = _ABBREVIATIONS | frozenset({"limited", "incorporated", "gmbh", "llp", "lp"})
#: An address inside a holder part: in <> or (), or bare, with no space in it.
_ADDRESS_TOKEN = re.compile(r"^[<(\[]?(?:[^\s<>()\[\]]+@[^\s<>()\[\]]+|(?:https?://|www\.)"
                            r"[^\s<>()\[\]]+|[a-z0-9-]+(?:\.[a-z0-9-]+)*\.[a-z]{2,}"
                            r"(?:/[^\s<>()\[\]]*)?)[>)\]]?[.,]?$", re.I)
#: A handle starts in lowercase and may have capitals later ("sindresorhus", "spotDL").
_HANDLE_TOKEN = re.compile(r"^[a-z0-9][A-Za-z0-9_.\-]{0,38}$")
_CAPITAL_TOKEN = re.compile(r"^(?:[A-ZÀ-Þ][A-Za-z0-9À-ɏ'._\-]*"
                            r"|[0-9][A-Za-z0-9]{0,7})$")
_HOLDER_SPLIT = re.compile(r"\s*,\s*|\s+and\s+|\s*&\s*|\s*\+\s*", re.I)
_HOLDERS_MAX = 4
#: "J.R.R" before "Tolkien": initials, not the end of a sentence.
_INITIALS = re.compile(r"^(?:[a-z]\.)+[a-z]$")
_HOLDER_TOKENS_MAX = 6


def _folded_words(text: str) -> list[str]:
    """*text* as lowercase words with accents folded ("Gebühr" -> "gebuhr",
    "Łukasz" -> "lukasz", a dotless "ı" -> "i")."""
    return re.findall(r"[^\W_]+", _fold(text).lower())


def _condition_free(text: str, exempt: frozenset = frozenset()) -> bool:
    """No condition word in *text* (addresses on the licence publishers' sites
    left out). A word in *exempt* (a word of the project's own name, see
    :func:`_identity`) is a name there ("Evil Martians" of ``evilmartians``)."""
    for word in _folded_words(_drop_licence_sites(text.lower())):
        if word in exempt:
            continue
        if (word in _CONDITION_WORDS or word.startswith(_CONDITION_STARTS)
                or any(part in word for part in _CONDITION_PARTS)):
            return False
    return True


# Who the project is -----------------------------------------------------------
#
# A copyright line may say who holds the copyright and nothing else. Whether a
# few capitalised words are a name or a condition cannot be told from the words
# alone, in any language ("Jane Doe, Corporations Barred"). So no word list decides it. A holder is trusted for its words only
# when it is the project itself: its words are the project's own account and
# repository names, read from the find's address, never from the file. A file
# may also name ONE holder that is not the project: one name alone, with
# nothing after it but that holder's own addresses. Two or more holders, when
# any of them is not the project, make the file custom: a name followed by
# more capitalised words is exactly what a condition written into the
# copyright line looks like.

#: The project's own words: ``(parts, whole)``. ``parts`` are the words of the
#: owner and repository names ("JaneDoe42" -> "jane", "doe", "janedoe42",
#: "42"); ``whole`` the names as one run of letters and digits.
_NO_ID: tuple = (frozenset(), ())
_IDENT_MAX = 4
_IDENT_CHARS_MAX = 100


def _fold(text: str) -> str:
    """Accents off and :data:`_LETTER_MAP` letters made plain; other letters stay."""
    import unicodedata
    return "".join(_LETTER_MAP.get(c, c) for c in unicodedata.normalize("NFKD", text)
                   if not unicodedata.combining(c) and unicodedata.category(c) != "Cf")


class _Ident(tuple):
    """:func:`_identity`'s ``(parts, whole)``, with ``owner``: the same words
    for the owner's name alone (the first name given). A holder tied to the
    owner is the person or group that owns the project, not its subject."""

    def __new__(cls, pair: tuple, owner: tuple):
        obj = tuple.__new__(cls, pair)
        obj.owner = owner
        return obj


def _owner_of(ident: tuple) -> tuple:
    return getattr(ident, "owner", _NO_ID)


class OwnerName(str):
    """The owner account's own public name, as its code host's account page
    gives it (GitHub's ``/users/<login>`` "name"), never read from the files.

    Without this, "Copyright (c) 2026 Jane Doe" on an account named
    otherwise, whose public name is Jane Doe, is marked "check this licence yourself: it names someone other than the
    author". A holder that is the account's own public name is the owner,
    as the account name itself is. Placed in an identity after the owner
    and the repository name, it counts as the owner's words.

    Residual (the same as the account name's, which the strict read already
    trusts): an account that names itself after a restriction no word list
    knows, and writes that name as the holder, reads as sure.
    """

    #: A plain public name, at most 60 long (GitHub allows 255; a person's name is short).
    _OK = re.compile(r"^[A-Za-z0-9À-ÖØ-öø-ɏ .'\-]{2,60}$")

    @classmethod
    def from_host(cls, value: Any) -> Optional["OwnerName"]:
        """The name, or None when it is missing or not written as a plain name
        (only letters, digits, spaces, dots, hyphens and apostrophes)."""
        if not isinstance(value, str):
            return None
        name = " ".join(value.split())
        return cls(name) if cls._OK.match(name) else None


def _identity(identity: Any) -> tuple:
    """The project's own words from *identity* (its owner and repository
    names, and the owner's :class:`OwnerName` when its code host gave one)."""
    if isinstance(identity, str):
        identity = (identity,)
    try:
        given = list(identity or ())[:_IDENT_MAX]
    except TypeError:
        return _NO_ID
    owner = given[:1] + [g for g in given[1:] if isinstance(g, OwnerName)]
    return _Ident(_identity_words(given), _identity_words(owner))


def _identity_words(given: list) -> tuple:
    parts: set[str] = set()
    whole: list[str] = []
    for raw in given:
        if not isinstance(raw, str) or not raw.strip():
            continue
        folded = _fold(raw[:_IDENT_CHARS_MAX])
        for piece in re.split(r"[^A-Za-z0-9]+", folded):
            if not piece:
                continue
            parts.add(piece.lower())
            parts.update(p.lower() for p in
                         re.findall(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|[0-9]+", piece))
        run = re.sub(r"[^a-z0-9]+", "", folded.lower())
        for w in (run, run.rstrip("0123456789")):
            if len(w) >= 2 and w not in whole:
                whole.append(w)
    return (frozenset(p for p in parts if len(p) >= 2 and not p.isdigit()), tuple(whole))


def _initials(words: list[str]) -> str:
    return "".join(w[0] for w in words if w)


def _run_is_project(run: list[str], ident: tuple) -> bool:
    """Is this run of a holder's words the project's own name?"""
    parts, whole = ident
    if not run:
        return False
    if len(run) == 1:
        w = run[0]
        return w in parts or w in whole or (len(w) >= 4 and any(w in x for x in whole))
    joined = "".join(run)
    if len(joined) >= 5 and any(joined in x for x in whole):
        return True                           # "Jane Doe" of janedoe, "Anton Brink" of antonbrinkcodes
    # "jdoe" (initials, then the last name), "waldoj" (a name, then initials)
    short = {_initials(run[:-1]) + run[-1], run[0] + _initials(run[1:]), _initials(run)}
    return any(len(s) >= 3 and (s in parts or s in whole) for s in short)


def _is_project(words: list[str], ident: tuple) -> bool:
    """Every word of *words* (folded; particles and company words aside) is the project's name."""
    words = [w for w in words if w not in _NAME_PARTICLES and w not in _COMPANY_WORDS]
    if not words or (not ident[0] and not ident[1]):
        return False
    n = len(words)
    reach = [False] * (n + 1)
    reach[0] = True
    for i in range(n):
        if not reach[i]:
            continue
        for j in range(i + 1, min(n, i + _HOLDER_TOKENS_MAX) + 1):
            if not reach[j] and _run_is_project(words[i:j], ident):
                reach[j] = True
    return reach[n]


#: Host and path words an address may carry without saying anything: mail
#: services, code hosts and the plain parts of a web address.
_ADDRESS_WORDS = frozenset("""
www mail email gmail googlemail outlook hotmail live msn yahoo ymail icloud me mac proton
protonmail pm gmx web posteo mailbox fastmail zoho yandex qq foxmail naver aol hey tutanota
users noreply reply github gitlab codeberg bitbucket sourceforge io com org net edu gov ac co
""".split())
#: Path words that only point at the licence or at the project's page.
_ADDRESS_PATH_WORDS = frozenset("""
license licence licenses licences mit copying readme md txt html htm index about home blob raw
tree main master docs doc page pages site
""".split())
#: What may be left of an address word once every known word is taken out of
#: it (initials and digits: "jdoe", "jane84").
_ADDRESS_REST_MAX = 3


def _piece_known(piece: str, words: frozenset, ident: tuple) -> bool:
    """One word of an address, taken apart into known words (the holder's own
    name words, the project's words), with at most a few letters left over."""
    if not piece or piece.isdigit() or piece in _ADDRESS_WORDS or len(piece) == 1:
        return True                           # a plain word, digits, or one initial ("j@m.example")
    if len(piece) >= 4 and any(piece in x for x in ident[1]):
        return True                           # "comma" of the project commaai
    known = sorted({w for w in set(words) | set(ident[0]) | set(ident[1]) if len(w) >= 2},
                   key=len, reverse=True)
    rest = piece
    for w in known:
        if len(w) >= 3 or w == rest:
            rest = rest.replace(w, "")
    rest = re.sub(r"[0-9]+", "", rest)
    return rest != piece and len(rest) <= _ADDRESS_REST_MAX


_MAIL_HANDLE = re.compile(r"^[a-z][a-z0-9]{1,29}$")


def _mail_handle(local: str) -> bool:
    """Is an e-mail address's local part one plain handle ("jd77"), which
    says nothing? Without this a real BSD-2-Clause reads as custom ("Jane
    Doe <jd77@mail.example>"): a local part would have to be made of the
    holder's or the project's own words.

    One lowercase token of letters and digits only (no dot, dash or
    underscore to join words with), with no restricting word, condition word
    or part, and no condition run together that the lists know ("nocorps",
    "noncommercial", "privateonly"). Its domain is still read word by word.
    The strict read marks any address "check this licence yourself"
    (:func:`_strict_holder_why`), so a handle can let a licence be shown,
    never ticked by itself. Residual, as for names: a restriction in a
    language no list knows, run together as one handle, reads as a handle."""
    if not _MAIL_HANDLE.match(local) or local in _RESTRICT_WORDS:
        return False
    return not (_condition_word(local) or _never_a_name(local) or _joined_condition(local)
                or any(w in local for w in _RESTRICT_WORDS if len(w) >= 4))


def _address_known(address: str, words: frozenset, ident: tuple) -> bool:
    """Is every word of a web or e-mail address a known word?

    An address says nothing when it is on a licence publisher's site, or when
    its words are the holder's own name, the project's own name, or plain
    address words ("jane.doe" at a mail service, "https://sindresorhus.com"). Any other
    word in it may be a condition ("www.corporations-barred.example").
    """
    if _licence_site(address):
        return True
    text = _fold(address.strip("<>()[]\"'").rstrip(".,;:")).lower()
    if "@" in text and "://" not in text:
        local, _, host = text.partition("@")
        path = ""
    else:
        if not re.match(r"^[a-z][a-z0-9+.-]*://", text):
            text = "https://" + text
        try:
            split = urllib.parse.urlsplit(text)
            host = split.hostname or ""
        except ValueError:
            return False
        local = ""
        path = " ".join((split.path, split.query, split.fragment))
    labels = [x for x in host.split(".") if x]
    if len(labels) < 2 or not re.fullmatch(r"[a-z]{2,24}", labels[-1]):
        return False                          # no top-level name: not an address we can read
    labels = labels[:-1]
    if len(labels) > 1 and labels[-1] in ("co", "com", "org", "net", "ac", "edu", "gov"):
        labels = labels[:-1]                  # "example.co.uk"
    if local and _mail_handle(local):
        local = ""                            # a handle, like a lowercase handle in a name
    pieces = [p for part in [local] + labels for p in re.split(r"[^a-z0-9]+", part) if p]
    pieces += [p for p in re.split(r"[^a-z0-9]+", path) if p and p not in _ADDRESS_PATH_WORDS]
    return all(_piece_known(p, words, ident) for p in pieces)


class _Holder(tuple):
    """One holder a copyright line names: ``(words, folded, addresses, aliases, tokens)``.

    ``words``: the name as licence words (what :func:`_names_fill` matches);
    ``folded``: its words with accents folded; ``addresses``: its web and
    e-mail addresses; ``aliases``: words written in brackets after it that are
    not an address ("(LN)", "(aka Expo)"); ``tokens``: the name as written,
    one entry a word, accents folded and capitals kept ("spotDL",
    "Developers"), read by :func:`_plain_holder`."""

    __slots__ = ()

    def __new__(cls, words, folded, addresses, aliases, tokens=()):
        return tuple.__new__(cls, (tuple(words), tuple(folded), tuple(addresses),
                                   tuple(tuple(a) for a in aliases), tuple(tokens)))

    words = property(lambda self: self[0])
    folded = property(lambda self: self[1])
    addresses = property(lambda self: self[2])
    aliases = property(lambda self: self[3])
    tokens = property(lambda self: self[4])

    def generic(self) -> bool:
        """The licence's own holder words ("the authors", "contributors"), no name."""
        return (all(w in _NAME_PARTICLES or w in _COMPANY_WORDS for w in self.folded)
                and not self.addresses and not self.aliases)


#: The words that say "also known as" before a holder's other name, in brackets.
_AKA_WORDS = frozenset({"aka", "formerly", "fka", "dba"})


def _holder_parts_known(h: _Holder, ident: tuple) -> bool:
    """A holder's addresses and bracketed words say nothing beyond its name:
    each is its own name (or its initials), the project's, or "aka" and one
    plain name ("650 Industries, Inc. (aka Expo)")."""
    own = frozenset(w for w in h.folded if len(w) >= 2)
    for alias in h.aliases:
        if not alias:
            return False
        if "".join(alias) == _initials(list(h.folded)) or _is_project(list(alias), ident) \
                or all(_piece_known(a, own, ident) and a not in _ADDRESS_WORDS for a in alias):
            continue
        if len(alias) == 2 and alias[0] in _AKA_WORDS and _aka_word_ok(alias[1]):
            continue
        return False
    return all(_address_known(a, own, ident) for a in h.addresses)


def _aka_word_ok(word: str) -> bool:
    """The one word after "aka" is a plain name: a word the shipped word list
    knows (a name or an ordinary word, so never a joined-up run of words),
    with no condition word, root or part in it.

    If the alias were read as lowercase words given a capital, a joined-up
    condition ("(aka NoCorps)", "(aka nocorps)", "(aka BigTechBarred)",
    "(dba NoMilitary)") would never be taken apart and would read as a name.
    A word the list does not know is never one here: it cannot be told from
    words run together."""
    if not word.isascii() or not word.isalpha() or _condition_word(word) \
            or word in _RESTRICT_WORDS or _never_a_name(word) or _joined_condition(word):
        return False
    return _word_kind(word) in ("name", "common")


def _holders_ok(holders: Any, ident: tuple) -> bool:
    """May these holders, every one the file names, stand next to a licence?

    The licence's own holder words always may. When the file names one holder,
    it is the project itself (:func:`_is_project`) or a plain name
    (:func:`_plain_holder`). When it names two or more, every one must be the
    project: a name followed by more capitalised words is exactly what a
    condition written into the copyright line looks like, in a language no
    list knows ("Jane Doe, Bez Firm", "Jane Doe, Hakuna Biashara, John Roe").
    Letting two or more plain names stand opens that route; it stays closed
    (the base rule), at the cost
    of real lines naming several people who are not the project
    (obsidian-tasks), which read as custom. In every case a holder's addresses
    and bracketed words must be its own or the project's.
    """
    named = [h for h in holders if not h.generic()]
    if not all(_holder_parts_known(h, ident) for h in named):
        return False
    named = list({h.folded: h for h in named}.values())    # one holder named twice is one
    if len(named) > 1:
        ok = all(_is_project(list(h.folded), ident) and _project_holder_ok(h) for h in named)
    else:
        ok = all(_project_holder_ok(h) if _is_project(list(h.folded), ident)
                 else _plain_holder(h, several=False, ident=ident) for h in named)
    if not ok or not _STRICT_NAMES.get():
        return ok
    # The second read (strict_names, the one that decides a pre-tick).
    # "Bu Mai", "La Yubaa", "Al Timkor" and "Indie Devs" as the only holder,
    # "Owen Kaisha Dame" of the account "owen", or "Ask First" of a
    # repository named "ask-first" must not read as sure MIT. Trusting a
    # stranger's name when the word list knows its words, or when the
    # owner's name vouches for one word, is not enough: a restriction transliterated from another
    # language is often made of syllables that are also names ("Bu Mai",
    # "Jin Mai"): no list can tell them apart. So no list decides it here.
    # A holder is sure only when it is the OWNER's account itself, word for
    # word (:func:`_is_project` with the owner alone: "Sindre Sorhus" of
    # sindresorhus, "Matheus Baldissara" of BaldissaraMatheus), with no
    # condition word in it, its addresses and bracketed words its own or the
    # owner's. The repository's name never vouches (it is the subject, and
    # anyone names it). Every other holder, however plain, is read and shown
    # and marked "check this licence yourself": never ticked by itself.
    #
    # Two word lists never decide a sure licence. If a holder made only of
    # the licence's own holder words (_NAME_PARTICLES, _COMPANY_WORDS:
    # "Individual Developers", "Community Others", "Individual Developers
    # Inc.") were skipped, or the owner's name with organisation, institution
    # or group words after it ("JaneDoe Individual Developers", "Liam Chen
    # School", "Liam Chen Open Source Projects") counted as the owner, both
    # would read as sure MIT and be pre-ticked. So EVERY holder the file names, the
    # licence's own holder words included, must be exactly the owner's
    # account or name, word for word, with nothing else added: no other
    # word, no company or group word, no bracketed word and no address
    # (:func:`_strict_holder_why`). Anything else is read and shown, and
    # marked to check, with the reason said (licence_check_why).
    owner = _owner_of(ident)
    return all(not _strict_holder_why(h, owner) for h in dict.fromkeys(holders))


def _run_is_owner(run: list[str], owner: tuple) -> bool:
    """Is this run of a holder's words the owner's account name, as spelled?
    One word: the whole account ("sindresorhus") or one of its own words
    ("Fahad" of Fahad-Dezloper), never a piece cut out of it ("Anna" of
    joannasmith, "csxiaoyao" of csxiaoyaojianxian). A full name of two words
    or more: spelled, in order, inside the account, six letters or more
    ("Akshita Gupta" of akshitagupta15june, "Anton Brink" of
    antonbrinkcodes). Never initials spelled out ("Waldo Jaquith" of waldoj):
    the word after an initial could be any word."""
    parts, whole = owner
    joined = "".join(run)
    if not joined:
        return False
    if len(run) == 1 or joined in whole:
        return joined in parts or joined in whole
    return len(joined) >= 6 and any(joined in x for x in whole)


def _run_like_owner(run: list[str], owner: tuple) -> bool:
    """Does this run look like part of the owner's account (four letters or
    more of it, "Waldo" of waldoj)? Only to say WHY a licence is marked
    ("added" rather than "stranger"); it never makes a holder sure."""
    joined = "".join(run)
    return _run_is_owner(run, owner) or (len(joined) >= 4
                                         and any(joined in x for x in owner[1]))


def _owner_exact(words: list[str], owner: tuple) -> bool:
    """Every word of *words* (folded, none left out) is the owner's account name."""
    if not words or (not owner[0] and not owner[1]):
        return False
    n = len(words)
    reach = [False] * (n + 1)
    reach[0] = True
    for i in range(n):
        if not reach[i]:
            continue
        for j in range(i + 1, min(n, i + _HOLDER_TOKENS_MAX) + 1):
            if not reach[j] and _run_is_owner(words[i:j], owner):
                reach[j] = True
    return reach[n]


#: Why a licence is marked to check: the first holder the strict read could
#: not take as the owner's account itself. In order of what she most needs to know.
CHECK_WHY = ("condition", "stranger", "added", "address", "general", "unsure")


def _strict_holder_why(h: _Holder, owner: tuple) -> str:
    """'' when holder *h* is exactly the owner's account or name with nothing
    else added; otherwise why not (one of :data:`CHECK_WHY`). No word list
    makes a holder sure: lists only make one less sure ("condition")."""
    words = list(h.folded)
    if not words:
        return "address" if h.addresses else "general"
    why = _words_why(words, owner)
    if why == "general" and h.addresses:
        return "address"                      # "Comma.ai, Inc.": an address and company words
    if why:
        return why
    if h.addresses:
        return "address"                      # even the owner's own: something added
    if h.aliases:
        return "added"                        # "Liam Chen (LC)", "(aka Expo)"
    token = _STRICT_NAMES.set(True)           # the strict rule, whoever asks
    try:
        if not _project_holder_ok(h) or _all_weak_conditions(list(h.tokens)):
            return "condition"
    finally:
        _STRICT_NAMES.reset(token)
    return ""


def _words_why(words: list[str], owner: tuple) -> str:
    if _owner_exact(words, owner):
        return ""
    n = len(words)
    if any(_run_like_owner(words[i:j], owner) for i in range(n)
           for j in range(i + 1, min(n, i + _HOLDER_TOKENS_MAX) + 1)):
        return "added"                        # the owner's name, and more words
    if all(w in _NAME_PARTICLES or w in _COMPANY_WORDS for w in words):
        return "general"                      # "Individual Developers", "the contributors"
    return "stranger"


def licence_holders_why(text: Any, *, identity: Any = ()) -> str:
    """Why the holders a licence or notice *text* names are not all exactly
    the owner's account: the first of :data:`CHECK_WHY` its copyright lines
    give, or '' when there is none. The lines before and after the licence
    first; when they give nothing, the copyright lines inside it (Apache's
    "Copyright [yyyy] [name of copyright owner]" filled in). Read the first
    (not strict) way, so it names the holders that read was given. It only
    says why; :func:`licence_to_check` decides."""
    if not isinstance(text, str) or len(text) > _LICENSE_TEXT_MAX:
        return ""
    lines = text.splitlines()
    if len(lines) > _LICENSE_LINES_MAX:
        return ""
    ident = _identity(identity)
    owner = _owner_of(ident)
    top, bottom = _edges(lines, ident)
    edge = lines[:top] + lines[max(bottom, top):]
    inner = lines[top:max(bottom, top)]
    for part in (edge, inner):
        whys: set[str] = set()
        for line in part:
            if len(line) > _EDGE_LINE_MAX_CHARS or not re.search(r"(?i)copyright|\(c\)|©", line):
                continue
            if _line_kind(line, ident) != "copyright":
                continue
            for h in _copyright_holders(_plain_line(line), ident) or ():
                why = _strict_holder_why(h, owner)
                if why:
                    whys.add(why)
        if whys:
            return next(w for w in CHECK_WHY if w in whys)
    return ""


def licence_check_why(text: Any, *, identity: Any = ()) -> str:
    """'' when the licence *text* is sure (or not read as a licence at all);
    otherwise why it is marked to check (:data:`CHECK_WHY`, "unsure" when
    no holder says why)."""
    if not licence_to_check(text, identity=identity):
        return ""
    return licence_holders_why(text, identity=identity) or "unsure"


def _project_holder_ok(h: _Holder) -> bool:
    """The project's own name, read as written: at most one condition word in
    it, counting the parts of a joined-up word, and none that shuts someone
    out or is about trade or licences ("NoCorps" of a project "nocorps" is
    "no corps")."""
    words = [p for t in h.tokens for p in _camel_parts(t)] + [w for w in h.folded]
    if any(_never_a_name(w) for w in words):
        return False
    if any(_joined_condition(w) and _word_kind(w) not in ("name", "common")
           for w in h.folded if not w.isdigit()):
        return False                          # "Nocorps" of a project "nocorps" (fix 1)
    conditions = {w for w in words if _condition_word(w) or w in _RESTRICT_WORDS}
    if len(conditions) > 1:
        return False
    if conditions and _STRICT_NAMES.get():
        # The second read (fix 3): a project's name with a condition word in
        # it ("Hobbyists Welcome" of hobbyists-welcome, and the real "Personal
        # Capital") is read and shown, but marked to check, never ticked.
        return False
    # A repository named after the condition ("corporations-keep-out",
    # "gratis-for-kids", "hobbyists-welcome") must not read as MIT. The one
    # condition word a project's name may hold, in a
    # name of three words or more (small words such as "for" counted), stands
    # beside names only ("Evil Martians" of evilmartians is two): with an
    # ordinary English word beside it, the name is a sentence ("Corporations
    # Keep Out", "Gratis For Kids", "Teams Owe Tribute"). A group or company
    # name may have one such word ("dsh-cost-meter contributors"), as a
    # stranger's may have one more. Two words stay a name ("Personal Capital"
    # of personalcapital, "Only Tech" of onlytech, both real lines):
    # the residual is a two-word project name of one condition word and one
    # ordinary word ("Hobbyists Welcome" of hobbyists-welcome). A name made
    # only of restriction words that are also names ("Kojin Riyou") is a
    # phrase here too.
    parts = [p for t in h.tokens for p in _camel_parts(t)]
    entity = bool(h.tokens) and (
        h.tokens[-1].lower().rstrip(".") in _COMPANY_WORDS or h.tokens[-1].lower() in _GROUP_WORDS
        or any(t.lower() in _INSTITUTION_WORDS for t in h.tokens))
    sized = [p for p in parts if not p.isdigit() and p.rstrip(".") not in _COMPANY_WORDS
             and p not in _INSTITUTION_WORDS and p not in _GROUP_WORDS]
    plain = [p for p in sized if p not in _PLAIN_PARTICLES]
    if _all_weak_conditions(plain):
        return False
    said = [p for p in plain if _condition_word(p) or p in _RESTRICT_WORDS]
    if said and len(sized) >= 3:
        beside = [_word_kind(p) for p in plain if p not in said]
        if any(k is None for k in beside) or beside.count("common") > (1 if entity else 0):
            return False
    return True


# Plain holders -------------------------------------------------------------------
#
# If one holder alone "may be any name", a condition written as
# capitalised words can stand in the copyright line ("Copyright 2024
# Jane Doe Corporations Barred", "... Corporations Barred", "... Imprese
# Escluse", "... NoCorps"), and the holder spot inside MIT then takes the same
# words. A holder that is not the project is read as a name only when it
# is plain, and anything else fails closed (the file is custom):
#
# * at most three name words (particles such as "van" or "de", company words
#   such as "Inc." and the organisation words below aside): a person's or a
#   studio's name, not a sentence. Four only when at least three of them are
#   names the word list knows ("Pedro Enrique Perez Navarro");
# * only plain Latin letters once accents are off (look-alike letters from
#   another alphabet end it);
# * a lowercase handle ("sindresorhus", "spotDL") only on its own;
# * no condition word in any language BuildBot knows, in the words or in the
#   parts of a joined-up word ("NoCorps", "BigTechBarred");
# * at most one ordinary English word that is never a name (the shipped
#   en_US word list): "Corporations Barred", "Big Tech Barred" and "Firms
#   Remunerate Author" are English sentences; "Sahib Singh", "Mark Gardner"
#   and "Sithu Win San" are names. A name written as a company, ending in
#   "Inc.", "LLC", "GmbH" and the like, may have two ("Fair Oaks Labs, Inc."). A word of the project's own name is not
#   counted ("Vehicle Maintenance Log Contributors" of vehicle-maintenance).
#
# What it cannot do: tell a name from two or three capitalised words of a
# language whose condition words are not in the list. That is the residual.

_PLAIN_NAME_MAX = 3
_PLAIN_NAME_LONG = 4
#: Words an organisation's name ends with; they are not counted as name words.
_ORG_WORDS = frozenset("""
software studio studios lab labs developers developer team teams foundation academy
softworks technologies technology systems solutions games apps industries media digital
works group project projects collective consulting design designs dev devs code coding
open source
""".split())
#: The words a group of people ends its name with ("The Go Authors"): the
#: name may then have two ordinary words, as a company's may (fix 2).
_GROUP_WORDS = frozenset("""
authors author contributors contributor developers developer maintainers team project community
""".split())
#: Words that name an institution ("Center for Open Science", "Khan Academy"):
#: organisation words that also let a name have two ordinary words, as a
#: company suffix does.
_INSTITUTION_WORDS = frozenset("""
foundation center centre institute university association society consortium initiative
alliance council college school academy laboratory laboratories
""".split())
#: Small words inside an organisation's name, not counted as name words.
_PLAIN_PARTICLES = _NAME_PARTICLES | frozenset({"for", "in"})


def _camel_parts(token: str) -> list[str]:
    """"NoCorps" -> ["no", "corps"]; "spotDL" -> ["spot", "dl"]."""
    return [p.lower() for p in re.findall(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|[0-9]+", token)]


def _condition_word(word: str) -> bool:
    return (word in _CONDITION_WORDS or word.startswith(_CONDITION_STARTS)
            or any(part in word for part in _CONDITION_PARTS))


#: Roots looked for INSIDE a word the word list does not know, which may be
#: words run together in lowercase or with one capital ("nocorps",
#: "Corpsbarred", "Seatspaid", "hobbyonly"). A strong root alone ends it; a
#: root saying who ("corp", "firm") ends it only with a root saying the rule
#: ("no", "only", "paid"), so "Acmecorp" or "Impresa Rossi" stay names. A
#: known word is not searched ("Barrett", "Seattle"), and a false hit only
#: makes the file custom.
_JOINED_STRONG = (
    "commerc", "comerc", "komerc", "komers", "kommerz", "barred", "banned", "forbid", "verbot",
    "verbod", "interdi", "prohib", "proib", "zakaz", "zabron", "kiell", "yasak", "nonprof",
    "noncom", "exclu", "esclu", "resale", "resell", "licens", "licenc", "lizenz", "royalt",
    "subscri", "paywall",
)
_JOINED_WHO = (
    "corp", "firm", "compan", "compagn", "business", "enterpr", "milit", "govt", "govern",
    "police", "student", "hobby", "personal", "private", "privat", "seat", "biashara", "kumpan",
    "kampuni", "negoti", "negosy", "ticar", "sirket", "syarikat", "perusahaan", "bedrijf",
    "unternehm", "entrepris", "empresa", "aziend", "impres", "societ", "foretag", "charit",
    "donat", "sponsor", "revenue", "ads", "kids", "school",
)
_JOINED_RULE = (
    "no", "non", "not", "only", "paid", "pay", "fee", "free", "sell", "sold", "out", "ban",
    "bar", "never", "except", "without", "hakuna", "bez", "sans", "sin", "kein", "nicht", "nur",
    "solo", "must", "buy", "cost", "price",
)


def _joined_condition(word: str) -> bool:
    """Does a word the list does not know hold a condition run together? (*word* lowercase)"""
    if any(root in word for root in _JOINED_STRONG):
        return True
    for who in _JOINED_WHO:
        at = word.find(who)
        if at < 0:
            continue
        rest = word[:at] + " " + word[at + len(who):]
        if any(rule in rest for rule in _JOINED_RULE):
            return True
    return False


def _word_kind(word: str) -> Optional[str]:
    """``"common"`` (an ordinary English word that is never a name), ``"name"``
    (a name the shipped en_US list knows) or ``"unknown"``; None when the list
    cannot be read (then no stranger's name of two or more words is trusted)."""
    try:
        from puffpai.catalogs import personal_words
        if personal_words.dict_kind("corporation") != "common":
            return None                       # the list is missing or unreadable
        kind = personal_words.dict_kind(word)
        if kind == "unknown":
            return "unknown"
        return "name" if personal_words.is_name_like(word) else "common"
    except Exception:  # noqa: BLE001 - a list that cannot be read trusts nothing
        return None


def _plain_words(tokens: list[str], *, several: bool, ident: tuple = _NO_ID) -> bool:
    """The rules above for a holder's words as written (see :func:`_plain_holder`)."""
    toks = [t.removeprefix("@") for t in tokens if t]
    if not toks or not all(t.isascii() for t in toks):
        return False
    name = [t for t in toks if t.lower() not in _PLAIN_PARTICLES
            and t.lower().rstrip(".") not in _COMPANY_WORDS and t.lower() not in _ORG_WORDS
            and t.lower() not in _INSTITUTION_WORDS]
    if len(name) > _PLAIN_NAME_LONG or (several and len(name) < 2):
        return False
    if len(name) > 1 and any(t[0].islower() for t in name):
        return False                          # a handle beside other words: a clause
    for t in toks:
        if any(_condition_word(p) for p in _camel_parts(t) + [t.lower()]):
            return False
        # A word the list does not know may be words run together: a
        # condition root inside it ends it ("nocorps", "Corpsbarred").
        for piece in re.split(r"[^a-z0-9]+", t.lower()):
            if piece and not piece.isdigit() and _word_kind(piece) != "name" \
                    and _word_kind(piece) != "common" and _joined_condition(piece):
                return False
    if len(name) >= 2:
        words = [t for t in name if not t.isdigit()]
        kinds = [_token_kind(t, ident) for t in words]
        # A name written as a company ("Fair Oaks Labs, Inc.", "Red Hat,
        # Inc.") or as a group ("The Home Assistant Authors") may have two
        # ordinary words; any other name one. An ordinary English word is
        # one the English condition list reads, so the group form reads like
        # the company form.
        entity = (toks[-1].lower().rstrip(".") in _COMPANY_WORDS
                  or any(t.lower() in _INSTITUTION_WORDS for t in toks)
                  or toks[-1].lower() in _GROUP_WORDS)
        if any(k is None for k in kinds) or kinds.count("common") > (2 if entity else 1):
            return False
        if len(name) > _PLAIN_NAME_MAX and kinds.count("name") < 3:
            return False
        # A name of three or more words does not end in an ordinary word
        # ("Jane Doe Tinkerers", "Jane Doe Amazon Nope": a name, then a
        # word about who may use it). A name ending in a holder or
        # organisation word ("Vehicle Maintenance Log Contributors") ends in
        # that word, not the ordinary one.
        if len(name) >= 3 and kinds and kinds[-1] == "common" and not entity \
                and toks[-1] == name[-1]:
            return False
    # Trusting a stranger's name only when every word is one the shipped
    # English list knows makes nearly every name from outside that list read
    # as custom (14 real licences of a 222-file corpus lost, 25 of 34 real
    # names refused, among them "Dan Abramov", "Hiroshi Tanaka", "Zalando
    # SE"). So a word no list knows is a name when the holder has a name's
    # shape (the rules above). A restriction written in Latin letters for another language ("Shougyou
    # Kinshi", "Mark Kinshi", "Tijari Mamnu"), is read by the condition lists:
    # their transliterations are condition words or parts, looked for in every
    # part of every word (_CONDITION_PARTS), and a holder made only of words
    # that are such a restriction word and also a name ("Kojin Riyou", "Rak
    # Ishi") is custom (_WEAK_CONDITION_WORDS). The residual is a restriction
    # in a language no list has, written as two or three capitalised words in
    # the holder's place, and nothing else on the line.
    words = [p for t in name for p in _joined_words(t) if not p.isdigit()]
    if len(words) >= 2:
        kinds = [_token_kind(t, ident) for t in words]
        if any(k is None for k in kinds):
            return False                      # the word list cannot be read: trust no stranger
        if _all_weak_conditions(words):
            return False
    # The second read (strict_names, see licence_to_check) trusts no
    # stranger's name at all: _holders_ok.
    return True


#: Set only inside :func:`strict_names` (the second read of :func:`licence_to_check`).
_STRICT_NAMES: "contextvars.ContextVar[bool]" = contextvars.ContextVar(
    "puffpai_licence_strict_names", default=False)


@contextlib.contextmanager
def strict_names():
    """Inside it, only the owner's account itself, word for word, may stand
    as a holder; any other holder makes the read differ, so the licence is
    marked to check. Per thread and task."""
    token = _STRICT_NAMES.set(True)
    try:
        yield
    finally:
        _STRICT_NAMES.reset(token)


def _vouching(ident: tuple) -> tuple:
    """Whose names may vouch for the words of an address beside a licence:
    the project's (owner and repository), or in the strict read the owner's
    account alone (fix 4: the repository's name is anyone's choice)."""
    return _owner_of(ident) if _STRICT_NAMES.get() else ident


def licence_to_check(text: Any, *, identity: Any = ()) -> bool:
    """Does *text* read as a licence only because it names a holder that is
    not the owner's account itself? BuildBot cannot tell a stranger's name
    ("Hiroshi Tanaka", "Jane Doe") from a restriction written in a language
    no list has, in the holder's place ("Shiyou Seigen", "Bu Mai"): it reads
    the licence, shows it, and never ticks it by itself. The same holds when the owner's own name, as
    the holder, has a condition word in it ("Personal Capital" of
    personalcapital). Only a licence with no holder, or with the owner's
    account itself as its only holder, word for word, is sure (the
    licence's own holder words, "the authors", are not)."""
    first = license_from_text(text, identity=identity)
    if first is None:
        return False
    with strict_names():
        return license_from_text(text, identity=identity) != first


def _all_weak_conditions(words: list[str]) -> bool:
    """Is every word (a holder's name words, written as given) a restriction
    word, or one of :data:`_WEAK_CONDITION_WORDS`, with at least one of those?"""
    low = [p for t in words for p in re.split(r"[^a-z0-9]+", _fold(t).lower()) if p]
    low = [p for p in low if p not in _PLAIN_PARTICLES and not p.isdigit()]
    if len(low) < 2 or not any(p in _WEAK_CONDITION_WORDS for p in low):
        return False
    return all(p in _WEAK_CONDITION_WORDS or _condition_word(p) or p in _RESTRICT_WORDS
               for p in low)


def _joined_words(token: str) -> list[str]:
    """"Shougyou-Kinshi" -> Shougyou, Kinshi (capitalised parts of three or
    more letters joined by "-", "_" or "."); any other token is one word
    ("CFX-Software", "Megabits_mzq", "J.R.R")."""
    parts = [p for p in re.split(r"[-_.]+", token) if p]
    caps = [p for p in parts if len(p) >= 3 and p.isalpha() and p[0].isupper()
            and p.lower() not in _ORG_WORDS and p.lower() not in _INSTITUTION_WORDS
            and p.lower().rstrip(".") not in _COMPANY_WORDS]
    return caps if len(caps) >= 2 else [token]


def _token_kind(token: str, ident: tuple) -> Optional[str]:
    """:func:`_word_kind` of one written word of a holder's name, where a word
    of the project's own name is a name, and a word joined with hyphens is
    read part by part ("Jonathan-David": two names; "Kaupan-Kielto": words no
    list knows)."""
    low = token.lower().strip(".")
    if _is_project([low], ident):
        return "name"
    kind = _word_kind(low)
    if kind != "unknown":
        return kind
    parts = [p for p in re.split(r"[-_']+", low) if p]
    if len(parts) < 2:
        return kind
    kinds = ["name" if _is_project([p], ident) else _word_kind(p) for p in parts]
    if any(k is None for k in kinds):
        return None
    if "unknown" in kinds:
        return "unknown"
    return "common" if "common" in kinds else "name"


def _plain_holder(h: _Holder, *, several: bool, ident: tuple = _NO_ID) -> bool:
    """Is this holder, which is not the project, a plain name? (rules above)"""
    if not h.tokens:
        # Only an address ("lichess.org"): plain when its words are known,
        # which _holder_parts_known has checked; never one of several.
        return bool(h.addresses) and not several
    return _plain_words(list(h.tokens), several=several, ident=ident)


def _holder_name(segment: str) -> Optional[tuple[str, ...]]:
    """One holder's name as licence words, or None when *segment* is not only a name."""
    tokens = segment.split()
    if not tokens or len(tokens) > _HOLDER_TOKENS_MAX:
        return None
    handles = 0
    name_parts: list[str] = []
    for token in tokens:
        if _ADDRESS_TOKEN.match(token):
            continue                          # an address: read by _address_known
        bare = _fold(token.strip("()[]<>\"'").rstrip(".,"))
        if not bare or not bare.isascii():
            return None                       # a letter that is not a plain Latin one
        low = bare.lower()
        if low in _NOT_NAME_WORDS or (bare == low and low in _NOT_NAME_LOWER):
            return None
        if low in _NAME_PARTICLES or low.rstrip(".") in _COMPANY_WORDS \
                or _CAPITAL_TOKEN.match(bare):
            name_parts.append(bare)
            continue
        if _HANDLE_TOKEN.match(bare.removeprefix("@")):
            handles += 1
            if handles > 1:
                return None                   # two lowercase words: a clause, not a name
            name_parts.append(bare.removeprefix("@"))
            continue
        return None
    words = tuple(_lic_norm(" ".join(name_parts)).split())
    if words and all(w in _NAME_PARTICLES for w in words):
        return None if len(words) > 3 else words   # "the authors", not "the and of the"
    return words


_BRACKETS = re.compile(r"\(([^()\[\]]*)\)|\[([^()\[\]]*)\]")
#: Condition words the project's own name never makes a name: words about
#: trade or licences and words that shut someone out, in any language listed.
_NEVER_A_NAME_STARTS = (
    "noncom", "nonprof", "prohib", "proib", "forbid", "forbud", "forbj", "verbot", "verbod",
    "interdi", "vieta", "zabron", "zakaz", "kiell", "tiltot", "yasak", "untersag", "exclu",
    "esclu", "ausgeschl", "ausschl", "uitgeslot", "utesl", "udeluk", "wyklucz", "vylouc",
    "kizar", "dikecual", "barred", "banned", "disallow", "denied", "excepted", "restrict",
)


def _never_a_name(word: str) -> bool:
    return word.startswith(_NEVER_A_NAME_STARTS) or any(part in word for part in _CONDITION_PARTS)


def _copyright_holders(line: str, ident: tuple = _NO_ID) -> Optional[tuple]:
    """The holders (:class:`_Holder`) of a copyright line, or None when it is not one.

    ``Copyright (c) 2019-2024 Holder Name <mail>. All rights reserved.``: a
    head, years, then only holder names (see :func:`_holder_name`), their
    addresses and bracketed words, with no condition word outside the
    addresses (a word of the project's own name is a name). Whether these
    holders may stand next to a licence is for :func:`_holders_ok` to say.
    """
    head = _COPYRIGHT_HEAD.match(line)
    if not head:
        return None
    rest = _YEARS.sub("", line[head.end():])
    rest = _RIGHTS.sub("", rest).strip().rstrip(".").strip()
    if len(rest) > 160 or not _HOLDER_CHARS.match(rest):
        return None
    if rest.lower().rstrip(":").strip() in ("notice", "notices", "statement"):
        return ()                             # a heading ("Copyright notice:"), no name in it
    for stop in re.finditer(r"\.\s+", rest):
        before = rest[:stop.start()].split()
        last = before[-1].lower() if before else ""
        if len(last) > 1 and last not in _ABBREVIATIONS and not _INITIALS.match(last):
            return None                       # a second sentence: not just a name
    if re.match(r"^by\s", rest, re.I):
        rest = rest[3:].strip()
    # Bracketed words: an address, or words said about the holder before them.
    brackets = [(m.start(), m.group(1) if m.group(1) is not None else m.group(2))
                for m in _BRACKETS.finditer(rest)]
    plain = _BRACKETS.sub(lambda m: " " * len(m.group(0)), rest)
    if any(c in plain for c in "()[]"):
        return None                           # a bracket left open, or one inside another
    said = [t for t in plain.split() if not _ADDRESS_TOKEN.match(t)]
    said += [t for _s, inside in brackets for t in inside.split() if not _ADDRESS_TOKEN.match(t)]
    if len(_lic_norm(" ".join(said)).split()) > 14:
        return None
    said_words = _folded_words(" ".join(said))
    # A word of the project's own name is a name ("Evil Martians" of
    # evilmartians, "dsh-cost-meter contributors"), but only ONE such word, and
    # never a word that shuts someone out or is about trade or licences
    # (_never_a_name): a project named "corporations-barred" does not make
    # "Corporations Barred" a name, nor one named "students-only" "Students
    # Only" (otherwise any word of the project's name but a trade or licence
    # word is taken as a name).
    exempt = frozenset(w for w in (said_words if _is_project(said_words, ident)
                                   else [x for x in said_words if _is_project([x], ident)])
                       if not _never_a_name(w))
    if sum(1 for w in exempt if _condition_word(w) or w in _RESTRICT_WORDS) > 1:
        exempt = frozenset()
    # The older English word list reads the words written in lowercase; a
    # capitalised word is a name ("Brian May", "Will Price") and is read by
    # the condition list instead.
    lower = [w for w in _lic_norm(" ".join(t for t in said if t == t.lower())).split()
             if w not in exempt]
    if not _clean(lower) or not _condition_free(" ".join(said), exempt):
        return None
    segments: list[tuple[int, str]] = []
    at = 0
    for m in _HOLDER_SPLIT.finditer(plain):
        segments.append((at, plain[at:m.start()]))
        at = m.end()
    segments.append((at, plain[at:]))
    merged: list[list] = []                   # [start, text]
    for start, seg in segments:
        if not seg.strip():
            continue
        low = seg.lower().strip().rstrip(".").strip()
        if merged and low in _COMPANY_WORDS:
            merged[-1][1] = merged[-1][1] + " " + seg      # "Fair Oaks Labs, Inc." is one holder
        else:
            merged.append([start, seg])
    if len(merged) > _HOLDERS_MAX:
        return None
    holders: list[list] = []                  # [words, folded, addresses, aliases, start]
    for start, seg in merged:
        name = _holder_name(seg)
        if name is None:
            return None
        seg_addresses = [t for t in seg.split() if _ADDRESS_TOKEN.match(t)]
        folded = _folded_words(" ".join(t for t in seg.split() if not _ADDRESS_TOKEN.match(t)))
        if not name and holders:
            holders[-1][2].extend(seg_addresses)            # "Jane Doe, https://jane.example"
            continue
        written = [_fold(t.strip("()[]<>\"'").rstrip(".,")) for t in seg.split()
                   if not _ADDRESS_TOKEN.match(t)]
        holders.append([name, folded, seg_addresses, [], start, [w for w in written if w]])
    if not holders and brackets:
        holders.append([(), [], [], [], 0, []])
    for start, inside in brackets:
        owner = next((h for h in reversed(holders) if h[4] <= start), holders[0])
        inner = inside.split()
        if inner and all(_ADDRESS_TOKEN.match(t) for t in inner):
            owner[2].extend(inner)
            continue
        alias = _folded_words(inside)
        if not alias or len(inner) > _HOLDER_TOKENS_MAX:
            return None
        owner[3].append(alias)
    return tuple(_Holder(h[0], h[1], h[2], h[3], h[5]) for h in holders)


def _names_fill(between: list[str], names: frozenset) -> bool:
    """*between* is whole names only: each part one of the licences' own holder
    words or one whole holder name from the file's copyright lines."""
    n = len(between)
    reach = [False] * (n + 1)
    reach[0] = True
    for i in range(n):
        if not reach[i]:
            continue
        if between[i] in _HOLDER_WORDS:
            reach[i + 1] = True
        for name in names:
            if name and tuple(between[i:i + len(name)]) == name:
                reach[i + len(name)] = True
    return reach[n]


#: A line that only says "All rights reserved." (the BSD licences put it on
#: its own line under the copyright line). Read only at the end of a
#: copyright line (:data:`_RIGHTS`), on its own line it would make a real
#: BSD-2-Clause file custom.
#: It adds no condition: it says the same as the copyright line above it.
_RIGHTS_LINE = re.compile(r"^all rights reserved\.?$", re.I)


def _line_kind(line: str, ident: tuple = _NO_ID) -> str:
    if len(line) > _EDGE_LINE_MAX_CHARS:
        return "other"
    plain = _plain_line(line)
    if not plain:
        return "blank"
    if _RIGHTS_LINE.match(plain):
        return "rights"
    if _SPDX_LINE.match(plain):
        return "spdx"
    if _URL_LINE.match(plain):
        # Its words are read again with the whole file's holders (every one
        # must be known, see license_from_text); a condition word ends it here.
        if _licence_site(plain) or (_clean(_lic_norm(plain).split(), title=True)
                                    and _condition_free(plain)):
            return "url"
        return "other"
    holders = _copyright_holders(plain, ident)
    if holders is not None and _holders_ok(holders, ident):
        return "copyright"
    if _is_title_line(plain):
        return "title"
    return "other"


#: The longest notice file read (a longer one is custom).
_NOTICE_TEXT_MAX = 20_000
#: Words an Apache NOTICE file uses to say what it is ("Licensed under ...",
#: "(MIT License)"), taken out before its prose is read for terms.
_NOTICE_OK_WORDS = frozenset("license licensed licenses licence licences attribution".split())


def notice_ok(text: Any, *, identity: Any = (), apache: bool = False) -> bool:
    """May a notice file (NOTICE, COPYRIGHT, PATENTS, ...) beside a project's
    licence stand without changing what the licence is? Fail closed.

    Yes when every line is blank, a plain copyright line or a web address of
    known words (the edge lines a licence may have, every holder read
    together with :func:`_holders_ok`). Under Apache-2.0 only (*apache*, and
    a file named NOTICE), prose may stand too: the licence itself says
    (section 4(d)) that the contents of the NOTICE file are for information
    only and do not modify the License. Its Apache notice sentences are taken
    out, and what is left may hold no restricting or condition word and no
    word in another script (a list, as a second guard; the licence's own
    words are the first). Anything else is no: the file is custom.

    A NOTICE, COPYING.LESSER, LICENSE-COMMERCIAL or LICENSES/NC.txt beside a
    clean MIT LICENSE is read too.
    """
    if not isinstance(text, str) or len(text) > _NOTICE_TEXT_MAX:
        return False
    if _CTRL_RE.search(text):
        return False
    if not text.strip():
        return True                           # an empty file says nothing
    lines = text.splitlines()
    ident = _identity(identity)
    kinds = [_line_kind(line, ident) for line in lines]
    if all(k in ("blank", "copyright", "url", "rights") for k in kinds):
        holders: list = []
        addresses: list[str] = []
        for line, kind in zip(lines, kinds):
            if kind == "copyright":
                holders.extend(_copyright_holders(_plain_line(line), ident) or ())
            elif kind == "url":
                addresses.append(_plain_line(line))
        if not _holders_ok(holders, ident):
            return False
        own = frozenset(w for h in holders for w in h.folded if len(w) >= 2)
        return all(_address_known(a, own, _vouching(ident)) for a in addresses)
    if not apache:
        return False
    norm = _lic_norm(text)
    for _gap, sentence in _APACHE_NOTICE:
        norm = norm.replace(sentence, " ")
    words = [w for w in norm.split() if w not in _NOTICE_OK_WORDS]
    return _clean(words) and not any(
        _condition_word(w) and not any(p in w for p in ("licen", "lizen")) for w in words)


def _spdx_ids(lines: list[str]) -> set[str]:
    out = set()
    for line in lines:
        m = _SPDX_LINE.match(_plain_line(line))
        if m:
            out.add(m.group(1).upper())
    return out


def _starts_with(norm: str, sentences, names: frozenset = frozenset()) -> Optional[int]:
    """:func:`_follow` with nothing at all allowed before the first sentence."""
    if not norm.startswith(sentences[0][1]):
        return None                           # the common case, answered without a search
    first_gap_zero = ((0, sentences[0][1]),) + tuple(sentences[1:])
    return _follow(norm, 0, first_gap_zero, first_is_title=False, names=names)


_EDGE_BEFORE = ("blank", "title", "copyright", "rights", "url", "spdx")
_EDGE_AFTER = ("blank", "copyright", "rights", "url", "spdx")


def _edges(lines: list[str], ident: tuple) -> tuple[int, int]:
    """``(top, bottom)``: lines[:k] may stand before a licence (k <= top) and
    lines[j:] after it (j >= bottom)."""
    top = 0
    while top < len(lines) and top < _EDGE_LINES_MAX             and _line_kind(lines[top], ident) in _EDGE_BEFORE:
        top += 1
    bottom = len(lines)
    while bottom > 0 and len(lines) - bottom < _EDGE_LINES_MAX             and _line_kind(lines[bottom - 1], ident) in _EDGE_AFTER:
        bottom -= 1
    return top, bottom


def licence_holders(text: Any, *, identity: Any = ()) -> tuple:
    """The holders a licence or notice text names on its copyright lines
    before and after the licence (the licences' own holder words left out).

    For :func:`holders_ok_together`: a project's licence and notice files
    are read together, so a real name in LICENSE and a phrase as the only
    holder of LICENSE.md or NOTICE is the same as "Jane Doe, Bez Firm" in
    one line."""
    if not isinstance(text, str) or len(text) > _LICENSE_TEXT_MAX:
        return ()
    lines = text.splitlines()
    if len(lines) > _LICENSE_LINES_MAX:
        return ()
    ident = _identity(identity)
    top, bottom = _edges(lines, ident)
    out = []
    for line in lines[:top] + lines[max(bottom, top):]:
        if _line_kind(line, ident) == "copyright":
            out.extend(h for h in (_copyright_holders(_plain_line(line), ident) or ())
                       if not h.generic())
    return tuple(out)


def holders_ok_together(holders: Any, *, identity: Any = ()) -> bool:
    """:func:`_holders_ok` for holders read from several files of one project."""
    return _holders_ok(list(holders or ()), _identity(identity))


def license_from_text(text: Any, *, identity: Any = ()) -> Optional[str]:
    """The SPDX id a LICENSE file reads as, or None when it is not one we recognise.

    A licence is recognised only by its own sentences, in order: all of them
    for the short licences (MIT, ISC, 0BSD, BSD, zlib, the Unlicense, WTFPL,
    the Apache notice), and every single word for the long ones (Apache,
    the GNU licences, MPL, CC0, OFL, Creative Commons 4.0), read from the
    ``licence_words`` part of ``config/where_to_look.json``, where only known
    wording differences and the Apache owner line are allowed. Between a
    short licence's sentences only numbering may stand; where a licence names
    its holder ("THE AUTHORS OR COPYRIGHT HOLDERS") only whole holder names
    may stand (:func:`_names_fill`).

    Before the sentences only whole lines that are a title ("MIT License"), a
    copyright line that holds only names (:func:`_copyright_holders`: the shape
    of a name, and no condition word in any of the languages it knows), a web
    address with no condition word, or an ``SPDX-License-Identifier`` line
    may stand; after them only copyright, address or SPDX lines. Anything
    else, in any language or wording, makes the file custom, and custom is
    None (a restriction written in German, in look-alike letters, without
    any listed word, or inside the copyright line itself must not pass). A text longer than
    :data:`_LICENSE_TEXT_MAX` characters is None, never cut. A text that names
    the Commons Clause or the Business Source License reads as that (not
    usable) whatever else it has. An SPDX line naming another licence is None.

    *identity* is who the project is: its owner and repository names, from the
    find's address (never from the file). The holders the file names are read
    with it (:func:`_holders_ok`): every holder is the project itself, or the
    file names one holder only. Web addresses next to the licence must be
    made of known words: the holders' own names, the project's, or plain
    address words (:func:`_address_known`).
    """
    if not isinstance(text, str) or not text.strip() or len(text) > _LICENSE_TEXT_MAX:
        return None
    lines = text.splitlines()
    if len(lines) > _LICENSE_LINES_MAX:
        return None
    ident = _identity(identity)
    # Every line is read into words once. Web and e-mail addresses never span a
    # line, so the words of lines[k:] joined are exactly _lic_norm of that text
    # (reading the whole text again for each of up to 61 starting lines is
    # too slow).
    words = [_lic_norm(line).split() for line in lines]
    starts = [0]                              # starts[k]: index in flat of line k's first word
    flat: list[str] = []
    for ws in words:
        flat.extend(ws)
        starts.append(len(flat))
    norm_all = " " + " ".join(flat) + " "
    for pattern, spdx in _RESTRICTIVE_NAMES:
        if re.search(pattern, norm_all):
            return spdx
    allowed_after = _EDGE_AFTER
    top, bottom = _edges(lines, ident)
    # The words that may follow the licence: those of lines[j:] for some j >= bottom.
    tails = _tails(words, bottom)
    # zlib's own text ends with its authors' names: for zlib only, lines of two
    # or more capitalised names may also follow it.
    signed = len(lines)
    while signed > 0 and len(lines) - signed < _EDGE_LINES_MAX and (
            _line_kind(lines[signed - 1], ident) in allowed_after
            or _is_names_line(lines[signed - 1])):
        signed -= 1
    signed_tails = _tails(words, signed)
    declared = _spdx_ids(lines[:top] + lines[bottom:])
    templates = [t for t in _LICENSE_TEMPLATES
                 if not declared or declared == {t[0].upper()}]
    long_editions = [e for e in _long_editions()
                     if not declared or declared == {e[0].upper()}]
    # Every holder the edge lines name, read together ("Jane Doe,
    # Corporations Barred" is not two names), and every
    # web address beside them made of known words.
    holders: list = []
    addresses: list[str] = []
    for line in lines[:top] + lines[bottom:]:
        kind = _line_kind(line, ident)
        if kind == "copyright":
            holders.extend(_copyright_holders(_plain_line(line), ident) or ())
        elif kind == "url":
            addresses.append(_plain_line(line))
    if not _holders_ok(holders, ident):
        return None
    holder_words = frozenset(w for h in holders for w in h.folded if len(w) >= 2)
    if not all(_address_known(a, holder_words, _vouching(ident)) for a in addresses):
        return None
    # A holder named inside a short licence is one of its own holder words or
    # a whole holder name from the file's own copyright lines.
    names = frozenset(h.words for h in holders if h.words)
    text_words = _LongText(lines, words, flat, starts, ident=ident, holders=tuple(holders))
    signers, signer_addresses = _signer_holders(lines[signed:bottom], ident)
    for k in range(0, top + 1):
        if k < top and not words[k]:
            continue                          # starting on a blank line reads the same as the next
        for spdx, elements in long_editions:
            for e in sorted(text_words.ends(starts[k], elements)):
                if tuple(flat[e:]) in tails:
                    return spdx
        norm = " " + " ".join(flat[starts[k]:]) + " "
        for spdx, sentences, closing in templates:
            end = _starts_with(norm, sentences, names)
            if end is None:
                continue
            ends = [end]
            if closing:
                end2 = _follow(norm, end, closing, first_is_title=False, names=names)
                if end2 is not None:
                    ends.append(end2)
            for e in ends:
                rest = tuple(norm[e:].split())
                if rest in tails:
                    return spdx
                # zlib's authors signing it are holders too, read with the others.
                if spdx == "Zlib" and rest in signed_tails and _holders_ok(holders + signers, ident):
                    known = frozenset(w for h in holders + signers for w in h.folded if len(w) >= 2)
                    if all(_address_known(a, known, _vouching(ident)) for a in signer_addresses):
                        return spdx
    return None


def _signer_holders(lines: list[str], ident: tuple) -> tuple[list, list[str]]:
    """The holders and web addresses of the lines after zlib's text: its
    signature lines ("Jean-loup Gailly   Mark Adler") and any copyright or
    address line between them."""
    out: list = []
    addresses: list[str] = []
    for line in lines:
        kind = _line_kind(line, ident)
        if kind == "copyright":
            out.extend(_copyright_holders(_plain_line(line), ident) or ())
        elif kind == "url":
            addresses.append(_plain_line(line))
        elif _is_names_line(line):
            for part in re.split(r"\s{2,}|\s+and\s+|,\s*", _plain_line(line)):
                if part.strip():
                    out.append(_Holder(_lic_norm(part).split(), _folded_words(part), (), (),
                                       [_fold(t) for t in part.split()]))
    return out, addresses


def _tails(words: list[list[str]], bottom: int) -> set[tuple[str, ...]]:
    """The words of ``lines[j:]`` for every j >= *bottom* (*words* holds each
    line's words), built from the end one line at a time. Only short edge lines
    are ever below *bottom*, so this stays small."""
    out: set[tuple[str, ...]] = {()}
    suffix: tuple[str, ...] = ()
    for j in range(len(words) - 1, bottom - 1, -1):
        suffix = tuple(words[j]) + suffix
        out.add(suffix)
    return out


# ---------------------------------------------------------------------------
# shipped vectors
# ---------------------------------------------------------------------------

VEC_MODEL = "all-MiniLM-L6-v2"
VEC_ARCHIVE_SHA256 = "913d7300ceae3b2dbc2c50d1de4baacab4be7b9380491c27fab7418616a16ec3"
VEC_DIM = 384
_VEC_CACHE: dict[str, tuple[Any, Any]] = {}


def snapshot_file(stem: str) -> Optional[str]:
    """The snapshot a vector file describes: ``<stem>.json(.gz)``; ``api_seeds`` is the seed list."""
    if not isinstance(stem, str) or not _STEM_RE.match(stem) or not _file_name_ok(stem):
        return None
    base = _config_dir()
    if stem == "api_seeds":
        path = os.path.join(base, "catalog_seeds", "apis.json")
        return path if os.path.isfile(path) else None
    for ext in (".json.gz", ".json"):
        path = os.path.join(base, "catalog_snapshots", stem + ext)
        if os.path.isfile(path):
            return path
    return None


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def snapshot_sha256(path: str) -> str:
    """The sha256 a vector file pins for its snapshot.

    A plain-text snapshot (``.json``) is hashed with its line endings made
    ``\\n``: a Windows checkout turns them into ``\\r\\n`` (``core.autocrlf``),
    and the same snapshot must match the same vectors on every computer. A
    ``.gz`` snapshot is binary and hashed as it is.
    """
    if path.endswith(".gz"):
        return _sha256(path)
    with open(path, "rb") as fh:
        data = fh.read()
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


def snapshot_vectors(stem: str) -> Optional[tuple[list[str], Any]]:
    """``(ids, vecs)`` shipped beside a snapshot (float16, n x 384, unit length), or None.

    Refused (None) when the file is missing or malformed, was made by another
    model or text shape, or was made for a different version of the snapshot.
    """
    snap = snapshot_file(stem)
    if snap is None:
        return None
    path = os.path.join(_config_dir(), "catalog_snapshots", stem + ".vec.npz")
    if not os.path.isfile(path):
        return None
    key = f"{path}|{os.path.getmtime(path)}|{os.path.getmtime(snap)}"
    if key in _VEC_CACHE:
        ids, vecs = _VEC_CACHE[key]
        return list(ids), vecs
    try:
        import numpy as np
        with np.load(path, allow_pickle=False) as z:
            ids, vecs, meta = z["ids"], z["vecs"], json.loads(str(z["meta"]))
    except (OSError, ValueError, KeyError):
        return None
    if not isinstance(meta, dict) or meta.get("model") != VEC_MODEL \
            or meta.get("archive_sha256") != VEC_ARCHIVE_SHA256 \
            or meta.get("text_shape") != code_mode.CANDIDATE_TEXT_SHAPE \
            or meta.get("snapshot_sha256") != snapshot_sha256(snap):
        return None
    if ids.dtype.kind != "U" or vecs.dtype != np.float16 or vecs.ndim != 2 \
            or vecs.shape[1] != VEC_DIM or vecs.shape[0] != ids.shape[0]:
        return None
    id_list = [str(i) for i in ids.tolist()]
    _VEC_CACHE[key] = (id_list, vecs)
    return list(id_list), vecs


def today() -> datetime.date:
    return datetime.datetime.now(datetime.timezone.utc).date()


__all__ = ["CUSTOM_LICENSE", "UNCHECKED_LICENSE", "OwnerName", "NOT_IN_BUILD_LINE", "NOTHING_SENT_LINE", "RegistryError", "SCHEMA", "adapter_fn",
           "details", "details_many", "find_local", "find_online", "key_ready",
           "last_changed_words", "live_rows_for",
           "licence_check_why", "licence_holders_why", "licence_to_check",
           "license_from_text", "license_words", "strict_names",
           "load_registry", "new_budget", "registry", "registry_path", "route", "row_by_id",
           "row_problems", "snapshot_file", "snapshot_vectors", "validate"]
