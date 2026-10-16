"""The "where to look" registry one validated file, routed by part kind.

What must hold:

* the shipped ``config/where_to_look.json`` loads and every row passes;
* validation refuses unknown fields, hosts that are not host names, unknown
  adapters, and query templates that use anything but ``{words}``,
  ``{min_stars}``, ``{since}``, ``{per_page}`` (or point off the row's hosts);
* each part kind routes to exactly the rows the spec lists, snapshots first;
* rows that are off are present with a reason, never routed, never searched;
* nothing in the code or registry names a goal.
"""
from __future__ import annotations

import copy
import json
import os
import re

import pytest

from puffpai.catalogs import where_to_look as w

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _shipped() -> dict:
    with open(os.path.join(REPO, "config", "where_to_look.json"), encoding="utf-8") as fh:
        return json.load(fh)


def _row(**over):
    base = copy.deepcopy(next(r for r in _shipped()["rows"] if r["id"] == "github_repo_search"))
    base.update(over)
    return base


def test_shipped_registry_loads_and_every_row_passes():
    rows = w.registry()
    assert len(rows) >= 30
    assert all(w.row_problems(r) == [] for r in rows)
    assert len({r["id"] for r in rows}) == len(rows)


@pytest.mark.parametrize("kind, ids", [
    ("code-app", ["awesome_selfhosted", "hacs", "mcp_registry", "github_repo_search", "codeberg",
                  "npm"]),
    ("cli", ["npm", "pypi"]),
    ("data-template", ["awesome_selfhosted", "github_repo_search", "huggingface"]),
    ("live-info", ["api_seeds", "public_apis", "apis_guru"]),
    ("knowledge", ["kiwix", "gutenberg", "openstax", "wikipedia"]),
    ("pictures", ["commons", "openverse"]),
    ("look", ["tweakcn", "shadcn"]),
    ("web-search", ["brave", "marginalia"]),
    ("subject", ["github_repo_search", "codeberg"]),
])
def test_routing_per_part_kind_snapshots_first(kind, ids):
    rows = w._route_all(kind)
    assert [r["id"] for r in rows] == ids
    roles = [r["role"] for r in rows]
    assert roles == sorted(roles, key=lambda role: role != "snapshot")
    # route() is the same list, less any place whose searcher is not in this build
    # and any place that needs the owner's own key while that key does not work.
    assert [r["id"] for r in w.route(kind)] == [r["id"] for r in rows
                                                if w.adapter_fn(r) is not None
                                                and w.key_ready(r)]


def test_route_never_names_a_place_that_cannot_be_asked(monkeypatch):
    # The "Start looking" intro reads route(): a place with no searcher in this
    # build must not be named as a place BuildBot asks.
    real = w.adapter_fn
    monkeypatch.setattr(w, "adapter_fn",
                        lambda row: None if row["id"] == "codeberg" else real(row))
    assert "codeberg" not in [r["id"] for r in w.route("code-app")]
    assert "codeberg" in [r["id"] for r in w._route_all("code-app")]
    assert "github_repo_search" in [r["id"] for r in w.route("code-app")]


def test_rows_that_are_off_are_present_with_a_reason_and_never_routed():
    off = {r["id"]: r for r in w.registry() if not r["enabled"]}
    assert set(off) == {"gitlab", "dockerhub", "software_heritage", "grep_app", "sourcegraph",
                        "pypi_search", "internet_archive", "mwmbl", "ecosystems",
                        "gutendex_live", "loc", "wikibooks"}
    for r in off.values():
        assert r["reason"].strip() and r["default_on"] is False
    routed = {r["id"] for k in w.KINDS for r in w.route(k)}
    assert not routed & set(off)


def test_keyed_web_search_is_never_default_on():
    for rid in ("brave", "marginalia"):
        row = w.row_by_id(rid)
        assert row["auth"] == "key" and row["default_on"] is False


def test_every_key_id_names_a_key_in_the_key_list():
    # A row's key must be one the key walkthrough knows, or it could never be used.
    import json
    with open(os.path.join(REPO, "config", "catalog_seeds", "keys.json"), encoding="utf-8") as fh:
        known = {k["id"] for k in json.load(fh)["keys"]}
    for r in w.registry():
        if r["key_id"] is not None:
            assert r["key_id"] in known, r["id"]


def test_a_place_that_needs_her_key_is_named_only_while_the_key_works(monkeypatch):
    # Web search runs only with her own key: with none, the intro never names it.
    assert w.route("web-search") == []
    from puffpai.security import keys
    monkeypatch.setattr(keys, "key_status",
                        lambda sid, **kw: {"state": "works" if sid == "brave_search" else "missing"})
    assert [r["id"] for r in w.route("web-search")] == ["brave"]
    monkeypatch.setattr(keys, "key_status", lambda sid, **kw: {"state": "locked"})
    assert w.route("web-search") == []

    def broken(sid, **kw):
        raise RuntimeError("index unreadable")

    monkeypatch.setattr(keys, "key_status", broken)
    assert w.route("web-search") == []                   # cannot check: not named, not asked
    assert w.key_ready({"auth": "none"}) is True


def test_github_rows_hosts_are_exactly_the_four_github_hosts():
    hosts = set()
    for rid in ("github_repo_search", "github_details", "github_codeload"):
        hosts |= set(w.row_by_id(rid)["hosts"])
    assert hosts == {"api.github.com", "raw.githubusercontent.com", "github.com",
                     "codeload.github.com"}
    assert w.row_by_id("github_repo_search")["hosts"] == ["api.github.com"]


# ---------------------------------------------------------------------------
# validation refuses
# ---------------------------------------------------------------------------

def test_refuses_unknown_fields():
    assert any("unknown fields" in p for p in w.row_problems(_row(secret_extra=1)))
    doc = _shipped()
    doc["extra_top"] = True
    with pytest.raises(w.RegistryError):
        w.validate(doc)


@pytest.mark.parametrize("host", ["https://api.github.com", "API.GITHUB.COM", "localhost",
                                  "140.82.112.6", "api.github.com/search", "a..b.com", "",
                                  "exa mple.com"])
def test_refuses_hosts_that_are_not_host_names(host):
    assert "hosts must be host names" in w.row_problems(_row(hosts=[host]))


@pytest.mark.parametrize("adapter", ["puffpai.catalogs.searchers.gitlab:search",
                                     "os:system", "puffpai.catalogs.searchers.github",
                                     "puffpai.catalogs.searchers.github:Search()",
                                     "puffpai.catalogs.searchersX.github:search",
                                     "puffpai.catalogs.searchers.github:__init__",
                                     "puffpai.catalogs.searchers.github:__dict__",
                                     "puffpai.catalogs.searchers.github:_optional",
                                     "puffpai.catalogs.searchers.__init__:fetch"])
def test_refuses_unknown_adapters(adapter):
    assert "unknown adapter" in w.row_problems(_row(adapter=adapter))


@pytest.mark.parametrize("template, why", [
    ("https://api.github.com/search/repositories?q={goal}", "not allowed"),
    ("https://api.github.com/search/repositories?q={words!r}", "not allowed"),
    ("https://api.github.com/search/repositories?q={words:>10}", "not allowed"),
    ("https://api.github.com/search/repositories?q={words.__class__}", "not allowed"),
    ("http://api.github.com/search/repositories?q={words}", "plain https"),
    ("https://evil.example.com/?q={words}", "not one of the row's hosts"),
    ("https://api.github.com:8443/?q={words}", "plain https"),
])
def test_refuses_unknown_template_fields_and_foreign_addresses(template, why):
    probs = w.row_problems(_row(query_template=template))
    assert any(why in p for p in probs), probs


def test_off_rows_need_a_reason_and_enabled_rows_may_not_carry_one():
    assert "a row that is off needs a reason" in w.row_problems(_row(enabled=False))
    assert "reason is only for rows that are off" in w.row_problems(_row(reason="why not"))


def test_duplicate_ids_refuse_the_whole_file():
    doc = _shipped()
    doc["rows"].append(copy.deepcopy(doc["rows"][0]))
    with pytest.raises(w.RegistryError, match="duplicate id"):
        w.validate(doc)


def test_a_broken_file_is_refused_whole(tmp_path):
    doc = _shipped()
    doc["rows"][3]["hosts"] = ["not a host"]
    path = tmp_path / "where_to_look.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(w.RegistryError):
        w.load_registry(str(path))


def test_snapshot_rows_send_nothing():
    for r in w.registry():
        if r["role"] == "snapshot":
            assert r["hosts"] == [] and r["query_template"] is None


# ---------------------------------------------------------------------------
# disabled rows are never searched
# ---------------------------------------------------------------------------

def test_disabled_rows_never_searched(monkeypatch):
    calls = []

    def spy(*a, **k):
        calls.append(a)
        return []

    rows = w.registry()
    for r in rows:
        if r["id"] in ("api_seeds", "github_repo_search"):
            r["enabled"] = False
            r["reason"] = "switched off for this test"
    monkeypatch.setattr(w, "registry", lambda: copy.deepcopy(rows))
    monkeypatch.setattr(w, "adapter_fn", lambda row: spy)
    job = {"id": "j1", "name": "budget", "parts": []}
    w.find_local(job, kind="live-info")
    assert all(r["id"] != "api_seeds" for r in w.route("live-info"))
    assert calls and len(calls) == 2        # public_apis and apis_guru only


#: Adapters that may be absent in a partial build (then skipped and nothing is sent).
#: Every enabled row of this build resolves (without this check, the web and
#: Wikipedia rows can name functions that do not exist).
_LATER = {"awesome_selfhosted:local", "hacs:local", "codeberg:search", "npm:search", "pypi:lookup",
          "huggingface:search", "commons:search", "openverse:search"}


def test_enabled_adapters_resolve_or_are_not_in_this_build_yet():
    for r in w.registry():
        if not r["enabled"]:
            continue
        short = r["adapter"].rsplit(".", 1)[1]
        if w.adapter_fn(r) is None:
            assert short in _LATER, r["adapter"]


# ---------------------------------------------------------------------------
# goal-agnostic
# ---------------------------------------------------------------------------

_GOAL_WORDS = re.compile(r"wedding|bride|garden|guitar|minecraft|pregnan|recipe|bakery|"
                         r"workout|subchi|bunny", re.I)
_L4_FILES = ["config/where_to_look.json", "src/puffpai/catalogs/where_to_look.py",
             "src/puffpai/catalogs/search_budget.py", "src/puffpai/catalogs/trust_floor.py",
             "src/puffpai/catalogs/code_mode.py"]


def test_no_goal_is_named_in_the_lane_code_or_registry():
    searchers = os.path.join(REPO, "src", "puffpai", "catalogs", "searchers")
    files = [os.path.join(REPO, *f.split("/")) for f in _L4_FILES]
    files += [os.path.join(searchers, n) for n in os.listdir(searchers) if n.endswith(".py")]
    for path in files:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        assert not _GOAL_WORDS.search(text), path


@pytest.mark.parametrize("snap", ["catalog_snapshots/CON", "catalog_snapshots/con.json",
                                  "catalog_snapshots/NUL.json.gz", "catalog_snapshots/COM1",
                                  "catalog_snapshots/lpt9.txt", "catalog_snapshots/aux",
                                  "catalog_snapshots/x.json.", "catalog_snapshots/a..b.json"])
def test_refuses_reserved_and_odd_snapshot_names(snap):
    row = copy.deepcopy(next(r for r in _shipped()["rows"] if r["id"] == "kiwix"))
    assert w.row_problems(row) == []
    row["snapshot"] = snap
    assert "bad snapshot" in w.row_problems(row)


@pytest.mark.parametrize("stem", ["CON", "nul", "com1.x", "x.", "a..b"])
def test_vector_stems_refuse_reserved_names(stem):
    assert w.snapshot_file(stem) is None and w.snapshot_vectors(stem) is None


def test_adapter_fn_returns_only_functions_written_in_that_module():
    row = _row(adapter="puffpai.catalogs.searchers.github:search")
    from puffpai.catalogs.searchers import github
    assert w.adapter_fn(row) is github.search
    # an imported module attribute (``re`` inside github.py) or a class is refused
    assert w.adapter_fn(_row(adapter="puffpai.catalogs.searchers.github:re")) is None
    assert w.adapter_fn(_row(adapter="puffpai.catalogs.searchers.github:json")) is None


def test_every_enabled_row_resolves_in_this_build():
    missing = [r["id"] for r in w.registry() if r["enabled"] and w.adapter_fn(r) is None]
    assert missing == []
