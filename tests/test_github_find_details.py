"""Details for a GitHub find: pin, licence file, page facts, no core allowance.

What must hold (recorded bodies only, no network):

* the version pin is parsed from a recorded git info/refs answer; a branch that
  is not in the first 64 KB costs exactly one core call (at most 3 a build);
* raw files are read at the pinned commit in the fixed order and probing stops
  as soon as the mode is decided;
* ``keeps_data`` is true for a page that uses localStorage and false for one
  that keeps nothing; inputs, code bytes and outside hosts are counted from text;
* only GitHub hosts are asked, with no key, and the caps hold.
"""
from __future__ import annotations

import json
import os
import re
import urllib.parse

import pytest

from puffpai import source_fetch
from puffpai.catalogs import search_budget, trust_floor, where_to_look as w
from puffpai.catalogs.searchers import github

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "recorded_web")
SHA = "752fb66ad999a9a18c42160b01d4d3b8e7fe931c"
GH_HOSTS = {"api.github.com", "raw.githubusercontent.com", "github.com", "codeload.github.com"}


def read(name: str) -> bytes:
    with open(os.path.join(FIX, name), "rb") as fh:
        return fh.read()


def tree_answer(files, folder: str = "", links=()) -> bytes:
    """GitHub's answer to ``git/trees/<sha>`` (not recursive) for *files*: the
    entries of one folder (the top when *folder* is empty). A folder's sha is
    :func:`folder_sha` of its path."""
    import hashlib
    prefix = folder + "/" if folder else ""
    entries: dict[str, dict] = {}
    for path, body in files.items():
        if not path.startswith(prefix):
            continue
        rest = path[len(prefix):]
        head, sep, _ = rest.partition("/")
        if sep:
            entries.setdefault(head, {"path": head, "mode": "040000", "type": "tree",
                                      "sha": folder_sha(prefix + head)})
        else:
            entries[head] = {"path": head, "mode": "120000" if path in links else "100644",
                             "type": "blob", "size": len(body),
                             "sha": hashlib.sha1(body).hexdigest()}
    return json.dumps({"sha": SHA, "tree": sorted(entries.values(), key=lambda e: e["path"]),
                       "truncated": False}).encode()


def folder_sha(path: str) -> str:
    import hashlib
    return hashlib.sha1(("folder:" + path).encode()).hexdigest()


class FakeNet:
    """*listing*: answer GitHub's top listing (``git/trees/<sha>``, not
    recursive) from *files*, as the code host would (
    file by file, a licence is confirmed only with a listing)."""

    def __init__(self, files: dict[str, bytes], refs: bytes | None = None, extra=(),
                 listing: bool = True, links=()):
        self.files, self.refs, self.extra = files, refs, list(extra)
        self.listing, self.links = listing, set(links)
        self.calls: list[dict] = []

    def __call__(self, url, *, allowed_hosts, max_bytes, service, honour_robots=True,
                 allow_truncated=False, headers=None, key_id=None, transport=None, resolver=None):
        split = urllib.parse.urlsplit(url)
        self.calls.append({"url": url, "host": split.hostname, "hosts": set(allowed_hosts),
                           "headers": dict(headers or {}), "key_id": key_id,
                           "max_bytes": max_bytes, "service": service})
        assert split.hostname in allowed_hosts
        top = re.fullmatch(r"/repos/[^/]+/[^/]+/git/trees/([0-9a-f]{40})", split.path)
        if self.listing and split.hostname == "api.github.com" and top and not split.query:
            folders = {folder_sha(p.rsplit("/", 1)[0]): p.rsplit("/", 1)[0]
                       for p in self.files if "/" in p}
            folder = "" if top.group(1) == SHA else folders.get(top.group(1))
            if folder is not None:
                return {"body": tree_answer(self.files, folder, self.links), "reason": "ok",
                        "status": 200, "truncated": False, "headers": {}}
        for pattern, answer in self.extra:
            if re.search(pattern, url):
                return {"body": answer, "reason": "ok", "status": 200, "truncated": False,
                        "headers": {}}
        if split.path.endswith("/info/refs") and self.refs is not None:
            body = self.refs
            cut = len(body) > max_bytes
            return {"body": body[:max_bytes], "reason": "ok", "status": 200,
                    "truncated": cut, "headers": {}}
        if split.hostname == "raw.githubusercontent.com":
            path = split.path.split("/", 4)[4]
            if path in self.files:
                return {"body": self.files[path], "reason": "ok", "status": 200,
                        "truncated": False, "headers": {}}
        return {"body": None, "reason": "The library answered 404", "status": 404,
                "truncated": False, "headers": {}}

    def raw_paths(self):
        return [c["url"].split(f"/{SHA}/", 1)[1] for c in self.calls
                if c["host"] == "raw.githubusercontent.com"]


def item(name="SubchiBeats/wedding-planner"):
    items = json.loads(read("github_search_items_recorded.json"))["items"]
    return next(i for i in items if i["full_name"] == name)


def found_for(it, part="code-app"):
    return github.found_from_item(it, part=part)


def budget(**kw):
    return search_budget.SearchBudget(wizard=kw.pop("wizard", True),
                                      clock=lambda: 1_790_000_000.0, sleeper=lambda s: None, **kw)


SUBCHI_FILES = {"README.md": read("subchibeats_README.md"), "LICENSE": read("subchibeats_LICENSE"),
                "index.html": read("subchibeats_index.html")}


@pytest.fixture
def recent(monkeypatch):
    """The recorded project changed 2026-06-18; hold 'today' near the recording."""
    import datetime
    monkeypatch.setattr(trust_floor, "_today", lambda: datetime.date(2026, 9, 27))


# ---------------------------------------------------------------------------
# the pin
# ---------------------------------------------------------------------------

def test_pin_parsed_from_recorded_info_refs():
    body = read("info_refs_subchibeats.pktline")
    assert github.sha_from_refs(body, "main") == SHA
    assert github.sha_from_refs(body, "gh-pages") is None
    assert github.sha_from_refs(body, "../main") is None


def test_a_truncated_answer_keeps_only_complete_lines():
    body = read("info_refs_subchibeats.pktline")
    assert github.sha_from_refs(body[:60], "main") is None      # cut inside the first ref line
    lines = github.pkt_lines(body)
    assert lines[0].startswith(b"# service=git-upload-pack")


def test_pin_uses_git_refs_and_no_core_call(monkeypatch):
    net = FakeNet(SUBCHI_FILES, refs=read("info_refs_subchibeats.pktline"))
    monkeypatch.setattr(source_fetch, "fetch_small", net, raising=False)
    b = budget()
    assert github.pin(found_for(item()), budget=b) == SHA
    call = net.calls[0]
    assert call["url"] == ("https://github.com/SubchiBeats/wedding-planner.git/info/refs"
                           "?service=git-upload-pack")
    assert call["max_bytes"] == 65536 and call["hosts"] == {"github.com"}
    assert b.spent() == {"github_git": 1}


def test_pin_falls_back_to_one_core_call(monkeypatch):
    refs = b"001e# service=git-upload-pack\n0000"          # the branch is not in the answer
    net = FakeNet({}, refs=refs, extra=[(r"/commits/main$", SHA.encode())])
    monkeypatch.setattr(source_fetch, "fetch_small", net, raising=False)
    b = budget()
    assert github.pin(found_for(item()), budget=b) == SHA
    core = net.calls[-1]
    assert core["url"] == "https://api.github.com/repos/SubchiBeats/wedding-planner/commits/main"
    assert core["headers"] == {"accept": "application/vnd.github.sha"}
    assert b.spent() == {"github_git": 1, "github_core": 1, "github_core_pin": 1}


def test_at_most_three_pin_fallbacks_a_build(monkeypatch):
    net = FakeNet({}, refs=b"0000", extra=[(r"/commits/", SHA.encode())])
    monkeypatch.setattr(source_fetch, "fetch_small", net, raising=False)
    b = budget()
    pins = []
    for n in range(5):
        it = dict(item(), name=f"p{n}", full_name=f"SubchiBeats/p{n}")
        pins.append(github.pin(found_for(it), budget=b))
    assert pins[:3] == [SHA] * 3 and pins[3:] == [None, None]
    assert b.spent()["github_core"] == 3


# ---------------------------------------------------------------------------
# raw files at the pin
# ---------------------------------------------------------------------------

def test_raw_probe_order_stops_early(monkeypatch, recent):
    net = FakeNet(SUBCHI_FILES, refs=read("info_refs_subchibeats.pktline"))
    monkeypatch.setattr(source_fetch, "fetch_small", net, raising=False)
    b = budget()
    rec = w.details(found_for(item()), budget=b)
    # One listing of the top at the pin (a core
    # call) says which files are there, so only those are asked: README and
    # LICENSE (every licence file there, here one), then index.html decides
    # it; package.json and the other licence names are not there, so not asked.
    assert net.raw_paths() == ["README.md", "LICENSE", "index.html"]
    assert rec["mode_guess"] == "static_web" and rec["repo"]["license_read"] == "MIT"
    # One listing, and one ask for the account's public name:
    # an addable find whose holder "Sahib Singh" is not the login SubchiBeats.
    core = [c["url"] for c in net.calls if c["host"] == "api.github.com"]
    assert len([u for u in core if "/git/trees/" in u]) == 1
    assert [u for u in core if "/git/trees/" not in u] == ["https://api.github.com/users/SubchiBeats"]
    assert b.spent()["github_core"] == 2


def test_without_a_listing_file_by_file_no_licence_is_confirmed(monkeypatch, recent):
    """Without the listing
    BuildBot cannot know which other licence files are there, so the read is
    custom (never GitHub's reported MIT), and no licence name is asked."""
    net = FakeNet(SUBCHI_FILES, refs=read("info_refs_subchibeats.pktline"), listing=False)
    monkeypatch.setattr(source_fetch, "fetch_small", net, raising=False)
    rec = w.details(found_for(item()), budget=budget())
    assert rec["repo"]["license_read"] == w.CUSTOM_LICENSE
    assert rec["trust"]["pretick_ok"] is False and rec["trust"]["path"] != "stars"
    assert net.raw_paths() == ["README.md", "package.json", "index.html"]


def test_readme_names_are_tried_in_order(monkeypatch):
    files = {"README": b"# Tool\n\n- Adds rows to a table quickly\n", "COPYING": b"x"}
    net = FakeNet(files, refs=read("info_refs_subchibeats.pktline"), listing=False)
    monkeypatch.setattr(source_fetch, "fetch_small", net, raising=False)
    w.details(found_for(item()), budget=budget())
    paths = net.raw_paths()
    assert paths[:3] == ["README.md", "readme.md", "README"]
    assert not set(paths) & set(github.LICENSE_NAMES)     # no listing: not confirmed, not asked


def test_with_a_listing_only_the_names_there_are_asked(monkeypatch):
    files = {"README": b"# Tool\n\n- Adds rows to a table quickly\n", "COPYING": b"x"}
    net = FakeNet(files, refs=read("info_refs_subchibeats.pktline"))
    monkeypatch.setattr(source_fetch, "fetch_small", net, raising=False)
    w.details(found_for(item()), budget=budget())
    assert net.raw_paths() == ["README", "COPYING"]


def test_details_of_the_recorded_single_page_tool(monkeypatch, recent):
    net = FakeNet(SUBCHI_FILES, refs=read("info_refs_subchibeats.pktline"))
    monkeypatch.setattr(source_fetch, "fetch_small", net, raising=False)
    rec = w.details(found_for(item()), budget=budget())
    repo, d = rec["repo"], rec["details"]
    assert repo["pinned_commit"] == SHA and re.match(r"^\d{4}-\d\d-\d\dT", repo["pinned_at"])
    assert repo["license_read"] == "MIT"
    assert d["entry_html"] == "index.html" and d["has_build_script"] is False
    assert d["keeps_data"] is True and d["inputs"] > 10 and d["code_bytes"] > 50_000
    assert d["root_markers"] == ["README.md", "LICENSE", "index.html"]
    assert d["package_json"] is None
    assert len(d["readme_text"].encode()) <= 64 * 1024
    assert any("Guest List" in line for line in d["feature_lines"])
    assert rec["friendly"] == "Bunny's Big Day"
    assert rec["integrable"] is True
    # "Sahib Singh" is not the account SubchiBeats, so the
    # licence reads and is marked "check this licence yourself".
    assert rec["trust"] == {"floor_ok": True, "path": "static_small", "pretick_ok": False,
                            "reasons": ["check_licence"]}
    assert {c["host"] for c in net.calls} <= GH_HOSTS
    assert all(c["key_id"] is None for c in net.calls)
    assert all(f"/{SHA}/" in c["url"] for c in net.calls if c["host"] == "raw.githubusercontent.com")


def test_keeps_data_false_for_a_page_with_no_storage():
    page = read("page_no_storage.html").decode()
    scripts = {"calc.js": read("page_no_storage_calc.js").decode()}
    assert github.local_scripts(page, "index.html") == ["calc.js"]
    facts = github.page_facts(page, scripts)
    assert facts["keeps_data"] is False
    assert facts["inputs"] == 3
    assert set(facts["runtime_hosts_guess"]) == {"cdn.example.org", "docs.example.net"}
    assert facts["code_bytes"] == len(scripts["calc.js"]) + len(
        "document.getElementById('bill').focus();")


def test_keeps_data_true_for_the_recorded_page():
    facts = github.page_facts(read("subchibeats_index.html").decode(), {})
    assert facts["keeps_data"] is True


def test_blob_download_counts_as_keeping_data():
    js = "const b = new Blob([json]); a.download = 'backup.json';"
    assert github.page_facts("<html></html>", {"app.js": js})["keeps_data"] is True


def test_local_scripts_never_leave_the_project():
    page = ('<script src="../../etc/x.js"></script><script src="//cdn.example.org/a.js"></script>'
            '<script src="data:text/javascript,1"></script><script src="/js/app.js?v=2"></script>'
            '<script src="js/one.js"></script>')
    assert github.local_scripts(page, "docs/index.html") == ["docs/js/app.js", "docs/js/one.js"]


def test_page_scripts_are_read_for_a_static_page(monkeypatch):
    files = {"index.html": b'<script src="app.js"></script><input>',
             "app.js": b"localStorage.setItem('k', 1)"}
    net = FakeNet(files, refs=read("info_refs_subchibeats.pktline"))
    monkeypatch.setattr(source_fetch, "fetch_small", net, raising=False)
    rec = w.details(found_for(item()), budget=budget())
    assert "app.js" in net.raw_paths() and rec["details"]["keeps_data"] is True


def test_package_json_with_build_and_lockfile_is_a_page_to_build(monkeypatch):
    files = {"package.json": json.dumps({"scripts": {"build": "vite build"},
                                         "dependencies": {"react": "1"}}).encode(),
             "package-lock.json": b"{}"}
    net = FakeNet(files, refs=read("info_refs_subchibeats.pktline"))
    monkeypatch.setattr(source_fetch, "fetch_small", net, raising=False)
    rec = w.details(found_for(item()), budget=budget())
    assert rec["mode_guess"] == "web_build"
    assert net.raw_paths()[-2:] == ["package.json", "package-lock.json"]   # stopped there


def test_server_dependency_decides_at_package_json(monkeypatch):
    files = {"package.json": json.dumps({"dependencies": {"express": "4"}}).encode()}
    net = FakeNet(files, refs=read("info_refs_subchibeats.pktline"))
    monkeypatch.setattr(source_fetch, "fetch_small", net, raising=False)
    rec = w.details(found_for(item()), budget=budget())
    assert rec["mode_guess"] == "server_app" and rec["integrable"] is False
    assert net.raw_paths()[-1] == "package.json"


def test_python_console_scripts_make_a_command_line_tool(monkeypatch):
    files = {"pyproject.toml": b'[project]\nname="x"\n[project.scripts]\nx = "x:main"\n'}
    net = FakeNet(files, refs=read("info_refs_subchibeats.pktline"))
    monkeypatch.setattr(source_fetch, "fetch_small", net, raising=False)
    rec = w.details(found_for(item()), budget=budget())
    assert rec["mode_guess"] == "cli_tool" and rec["integrable"] is True


def test_data_template_tree_counts_cost_core_and_are_capped(monkeypatch):
    tree = json.dumps({"tree": [{"type": "blob", "path": f"data/{n}.csv"} for n in range(60)]
                       + [{"type": "blob", "path": "README.md"}]}).encode()
    net = FakeNet({}, refs=read("info_refs_subchibeats.pktline"), extra=[(r"/git/trees/", tree)])
    monkeypatch.setattr(source_fetch, "fetch_small", net, raising=False)
    b = budget()
    got = []
    for n in range(7):
        # No licence file (GitHub says so): no top listing is asked, so only
        # the tree counts spend core calls.
        it = dict(item(), name=f"d{n}", full_name=f"SubchiBeats/d{n}", license=None)
        got.append(w.details(found_for(it, part="data-template"), budget=b))
    assert got[0]["mode_guess"] == "data_template" and got[0]["integrable"] is True
    assert b.spent()["github_core_tree"] == 5
    assert len([c for c in net.calls if "/git/trees/" in c["url"]]) == 5
    assert all("tree" not in g["details"] for g in got)       # found/v1 details stay exact


def test_top_listings_share_the_core_allowance_and_fail_closed_when_it_is_spent(monkeypatch,
                                                                               recent):
    """A listing of the top is one core call from
    the build's allowance; once it is spent, a licence read file by file is
    custom (never the reported licence), and nothing more is asked of core."""
    net = FakeNet(SUBCHI_FILES, refs=read("info_refs_subchibeats.pktline"))
    monkeypatch.setattr(source_fetch, "fetch_small", net, raising=False)
    b = budget()
    cap = search_budget.CAPS_WIZARD["github_core"]
    # The rest is kept for data finds; one core call asks the
    # account's public name (once a build for the one account here).
    listings = cap - github.LISTING_CORE_RESERVE - 1
    reads = []
    for n in range(listings + 2):
        it = dict(item(), name=f"p{n}", full_name=f"SubchiBeats/p{n}")
        reads.append(w.details(found_for(it), budget=b)["repo"]["license_read"])
    # Once the allowance is spent the licence is "not checked"
    # (still never usable), not "terms of its own".
    assert reads[:listings] == ["MIT"] * listings
    assert reads[listings:] == [w.UNCHECKED_LICENSE] * 2
    assert b.spent()["github_core"] == listings + 1
    assert len([c for c in net.calls if c["url"].endswith("/users/SubchiBeats")]) == 1


def test_details_cap_per_build(monkeypatch):
    net = FakeNet(SUBCHI_FILES, refs=read("info_refs_subchibeats.pktline"))
    monkeypatch.setattr(source_fetch, "fetch_small", net, raising=False)
    b = budget(caps=dict(search_budget.CAPS_WIZARD, details=2))
    recs = [w.details(found_for(item()), budget=b) for _ in range(3)]
    assert [r["repo"]["pinned_commit"] is not None for r in recs] == [True, True, False]
    assert recs[2]["details"]["entry_html"] is None           # nothing read for the third
    assert recs[2]["mode_guess"] == "unknown" and recs[2]["integrable"] is False
    assert recs[2]["trust"]["floor_ok"] is False            # no pin: never static_small


def test_raw_cap_stops_probing(monkeypatch):
    net = FakeNet(SUBCHI_FILES, refs=read("info_refs_subchibeats.pktline"))
    monkeypatch.setattr(source_fetch, "fetch_small", net, raising=False)
    b = budget(caps=dict(search_budget.CAPS_WIZARD, github_raw=2))
    rec = w.details(found_for(item()), budget=b)
    assert net.raw_paths() == ["README.md", "LICENSE"]
    assert rec["mode_guess"] != "static_web"


def test_cached_files_are_not_asked_again(monkeypatch):
    net = FakeNet(SUBCHI_FILES, refs=read("info_refs_subchibeats.pktline"))
    monkeypatch.setattr(source_fetch, "fetch_small", net, raising=False)
    w.details(found_for(item()), budget=budget())
    first = len(net.calls)
    b = budget()
    w.details(found_for(item()), budget=b)
    assert len(net.calls) == first and b.spent() == {"details": 1}


def test_archive_address_only_at_a_pinned_commit():
    rec = found_for(item())
    assert github.archive_url(rec) is None
    rec["repo"]["pinned_commit"] = SHA
    assert github.archive_url(rec) == ("https://codeload.github.com/SubchiBeats/wedding-planner"
                                       f"/tar.gz/{SHA}")


# ---------------------------------------------------------------------------
# untrusted text cannot make the reader slow or crash it
# ---------------------------------------------------------------------------

def test_a_hostile_page_is_read_in_linear_time():
    import time
    page = ("<script " + "a" * 30) * 12000 + "<title" * 20000 + "![" * 20000
    t = time.perf_counter()
    github.page_facts(page, {})
    github.local_scripts(page, "index.html")
    github.friendly_from(page, "# " + "![" * 20000, "fallback")
    github.feature_lines(("- " + "![x" * 30000 + "\n") * 3)
    assert time.perf_counter() - t < 2.0


def test_deeply_nested_json_and_toml_are_refused_quietly():
    deep = b"[" * 200_000 + b"]" * 200_000
    assert github.package_facts(deep) == {"scripts": [], "bin": False, "deps": []}
    assert github.python_console_scripts("pyproject.toml", b"a = " + b"[" * 100_000) is False


def test_script_tags_split_src_and_inline():
    tags = github.script_tags('<SCRIPT SRC="a.js"></SCRIPT><script>var x=1;</script><script')
    assert tags == [("a.js", ""), (None, "var x=1;")]


DETAIL_KEYS = {"readme_text", "feature_lines", "entry_html", "keeps_data", "inputs",
               "has_build_script", "code_bytes", "runtime_hosts_guess", "root_markers",
               "package_json", "page_hits"}


def test_details_carry_exactly_the_found_v1_keys(monkeypatch):
    files = {"pyproject.toml": b'[project]\nname="x"\n[project.scripts]\nx = "x:main"\n'}
    net = FakeNet(files, refs=read("info_refs_subchibeats.pktline"))
    monkeypatch.setattr(source_fetch, "fetch_small", net, raising=False)
    rec = w.details(found_for(item()), budget=budget())
    assert set(rec["details"]) == DETAIL_KEYS
    assert rec["mode_guess"] == "cli_tool"          # decided with the console scripts read


def test_a_mode_sent_in_with_the_record_is_not_trusted(monkeypatch):
    net = FakeNet({}, refs=read("info_refs_subchibeats.pktline"))
    monkeypatch.setattr(source_fetch, "fetch_small", net, raising=False)
    rec = found_for(item())
    rec["mode_guess"], rec["integrable"] = "static_web", True
    out = w.details(rec, budget=budget(caps=dict(search_budget.CAPS_WIZARD, details=0)))
    assert out["mode_guess"] != "static_web" and out["integrable"] is False


def forged(it=None):
    """A record carrying a pin, a licence read and page facts it never earned here."""
    rec = found_for(it or item())
    rec["repo"].update(stars=0, pinned_commit="a" * 40, pinned_at="2026-09-01T00:00:00Z",
                       license_read="MIT", pushed_at="2026-09-01")
    rec["details"] = dict(rec.get("details") or {}, entry_html="index.html",
                          has_build_script=False, keeps_data=True, root_markers=["index.html"])
    rec["mode_guess"], rec["integrable"] = "static_web", True
    return rec


@pytest.mark.parametrize("why", ["offline", "refused", "details_cap"])
def test_facts_read_at_a_pin_are_never_taken_from_the_record(monkeypatch, why):
    """Only facts read at a pin in this call count (static_small)."""
    if why == "offline":
        monkeypatch.setattr(source_fetch, "fetch_small",
                            lambda url, **k: {"body": None, "reason": "offline", "status": None,
                                              "truncated": False, "headers": {}}, raising=False)
    else:
        monkeypatch.setattr(source_fetch, "fetch_small",
                            FakeNet({}, refs=None) if why == "refused"
                            else FakeNet(SUBCHI_FILES, refs=read("info_refs_subchibeats.pktline")),
                            raising=False)
    caps = dict(search_budget.CAPS_WIZARD, details=0) if why == "details_cap" else None
    out = w.details(forged(), budget=budget(caps=caps) if caps else budget())
    repo = out["repo"]
    assert (repo["pinned_commit"], repo["pinned_at"], repo["license_read"]) == (None, None, None)
    assert out["details"]["entry_html"] is None and out["details"]["keeps_data"] is False
    assert out["mode_guess"] == "unknown" and out["integrable"] is False
    assert out["trust"]["floor_ok"] is False and out["trust"]["path"] == ""


def test_a_forged_pin_is_replaced_by_the_one_read(monkeypatch, recent):
    net = FakeNet(SUBCHI_FILES, refs=read("info_refs_subchibeats.pktline"))
    monkeypatch.setattr(source_fetch, "fetch_small", net, raising=False)
    out = w.details(forged(), budget=budget())
    assert out["repo"]["pinned_commit"] == SHA


def test_a_code_find_with_nothing_runnable_gets_a_tree_listing(monkeypatch):
    tree = json.dumps({"tree": [{"type": "blob", "path": f"lists/{n}.txt"} for n in range(60)]
                       + [{"type": "blob", "path": "README.md"}]}).encode()
    net = FakeNet({"README.md": b"# words"}, refs=read("info_refs_subchibeats.pktline"),
                  extra=[(r"/git/trees/", tree)])
    monkeypatch.setattr(source_fetch, "fetch_small", net, raising=False)
    it = dict(item(), language="C#")
    b = budget()
    rec = w.details(found_for(it), budget=b)
    assert rec["mode_guess"] == "data_template" and b.spent()["github_core_tree"] == 1
    # a find with a page (something runnable) never spends a tree call
    net2 = FakeNet(SUBCHI_FILES, refs=read("info_refs_subchibeats.pktline"),
                   extra=[(r"/git/trees/", tree)])
    monkeypatch.setattr(source_fetch, "fetch_small", net2, raising=False)
    b2 = budget()
    w.details(found_for(item()), budget=b2)
    assert "github_core_tree" not in b2.spent()


class Blocking(FakeNet):
    """GitHub refuses one project with an ordinary 403 (no rate-limit headers)."""

    def __call__(self, url, **kw):
        if "/blocked-repo" in url:
            self.calls.append({"url": url, "host": urllib.parse.urlsplit(url).hostname,
                               "headers": dict(kw.get("headers") or {}), "key_id": None})
            return {"body": b"Repository access blocked", "status": 403, "reason": "ok",
                    "truncated": False,
                    "headers": {"content-type": "text/plain; charset=utf-8",
                                "x-ratelimit-remaining": "4999"}}
        return super().__call__(url, **kw)


def test_one_blocked_project_does_not_stop_pinning_the_rest(monkeypatch, recent):
    net = Blocking(SUBCHI_FILES, refs=read("info_refs_subchibeats.pktline"))
    monkeypatch.setattr(source_fetch, "fetch_small", net, raising=False)
    lines: list[str] = []
    b = search_budget.SearchBudget(wizard=True, clock=lambda: 1_790_000_000.0,
                                   sleeper=lambda s: pytest.fail("no wait for a plain refusal"))
    blocked = found_for(dict(item(), name="blocked-repo", full_name="Someone/blocked-repo",
                             owner={"login": "Someone"}))
    d0 = w.details(blocked, budget=b)
    assert d0["repo"]["pinned_commit"] is None
    assert not b.later("github_git") and not b.later("github_core")
    assert "github_core" not in b.spent()             # no core call wasted on it
    d = w.details(found_for(item()), budget=b)
    assert d["repo"]["pinned_commit"] == SHA
    assert d["mode_guess"] == "static_web" and d["trust"]["path"] == "static_small"
    assert not any("busy" in line for line in lines)


def _older_path(monkeypatch, answers):
    """No fetch_small in this build: the older get_small answers by URL pattern."""
    monkeypatch.delattr(source_fetch, "fetch_small", raising=False)
    seen = []

    def get_small(url, *, allowed_hosts, max_bytes, honour_robots=True, service=""):
        seen.append({"url": url, "max_bytes": max_bytes})
        for pattern, body in answers:
            if re.search(pattern, url):
                if body is None:
                    return None, "The library answered 404, so there is nothing to read."
                if len(body) > max_bytes:
                    return None, "The library sent more than this lookup allows. Refused."
                return body, "ok"
        return None, "The library answered 404, so there is nothing to read."
    monkeypatch.setattr(source_fetch, "get_small", get_small)
    return seen


def _pkt(line: bytes) -> bytes:
    return b"%04x" % (len(line) + 4) + line


def test_older_path_reads_a_long_refs_answer_and_keeps_the_first_64_kb(monkeypatch):
    head = _pkt(b"# service=git-upload-pack\n") + b"0000"
    head += _pkt(SHA.encode() + b" refs/heads/main\x00caps\n")
    pulls = b"".join(_pkt(b"%040x refs/pull/%d/head\n" % (n, n)) for n in range(1500))
    body = head + pulls + b"0000"
    assert len(body) > 65536
    seen = _older_path(monkeypatch, [(r"/info/refs", body)])
    b = budget()
    assert github.pin(found_for(item()), budget=b) == SHA
    assert b.spent() == {"github_git": 1} and len(seen) == 1


def test_older_path_pin_fallback_uses_the_small_ref_answer(monkeypatch):
    ref = json.dumps({"ref": "refs/heads/main", "node_id": "x", "url": "u",
                      "object": {"sha": SHA, "type": "commit", "url": "u"}}).encode()
    refs = _pkt(b"# service=git-upload-pack\n") + b"0000"
    seen = _older_path(monkeypatch, [(r"/info/refs", refs), (r"/git/ref/heads/main$", ref)])
    b = budget()
    assert github.pin(found_for(item()), budget=b) == SHA
    assert [s["url"].split("github.com", 1)[1] for s in seen] == [
        "/SubchiBeats/wedding-planner.git/info/refs?service=git-upload-pack",
        "/repos/SubchiBeats/wedding-planner/git/ref/heads/main"]
    assert b.spent() == {"github_git": 1, "github_core": 1, "github_core_pin": 1}


def test_older_path_never_asks_for_an_answer_that_needs_a_header(monkeypatch):
    from puffpai.catalogs import searchers as S
    seen = _older_path(monkeypatch, [])
    b = budget()
    got = S.fetch("https://api.github.com/repos/o/r/commits/main", hosts=["api.github.com"],
                  max_bytes=4096, service="code_search", pool="github_core", budget=b,
                  accept="application/vnd.github.sha")
    assert got["reason"] == "needs headers" and seen == [] and b.spent() == {}


def test_a_ref_answer_for_another_branch_is_not_a_pin(monkeypatch):
    ref = json.dumps({"ref": "refs/heads/other", "object": {"sha": SHA, "type": "commit"}}).encode()
    refs = _pkt(b"# service=git-upload-pack\n") + b"0000"
    _older_path(monkeypatch, [(r"/info/refs", refs), (r"/git/ref/heads/main$", ref)])
    assert github.pin(found_for(item()), budget=budget()) is None


def test_code_finds_leave_tree_listings_for_a_later_data_search(monkeypatch):
    tree = json.dumps({"tree": [{"type": "blob", "path": f"lists/{n}.txt"} for n in range(60)]
                       + [{"type": "blob", "path": "README.md"}]}).encode()
    net = FakeNet({"README.md": b"# words"}, refs=read("info_refs_subchibeats.pktline"),
                  extra=[(r"/git/trees/", tree)])
    monkeypatch.setattr(source_fetch, "fetch_small", net, raising=False)
    b = budget()
    for n in range(5):                       # five code finds with nothing runnable
        it = dict(item(), name=f"c{n}", full_name=f"SubchiBeats/c{n}", language="C#")
        w.details(found_for(it), budget=b)
    assert b.spent()["github_core_tree"] == 3 and b.spent()["github_core_tree_code"] == 3
    it = dict(item(), name="d0", full_name="SubchiBeats/d0")
    rec = w.details(found_for(it, part="data-template"), budget=b)
    assert b.spent()["github_core_tree"] == 4 and rec["mode_guess"] == "data_template"


# ---------------------------------------------------------------------------
# a small project is read as one archive (for reading capacity)
# ---------------------------------------------------------------------------

def _zip(files: dict[str, bytes], top: str = "wedding-planner-main", extra=(),
         comment: bytes = SHA.encode()) -> bytes:
    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.comment = comment
        z.writestr(top + "/", b"")
        for path, body in files.items():
            z.writestr(f"{top}/{path}", body)
        for info, body in extra:
            z.writestr(info, body)
    return buf.getvalue()


def _zip_net(body: bytes) -> FakeNet:
    return FakeNet({}, refs=read("info_refs_subchibeats.pktline"),
                   extra=[(r"codeload\.github\.com/SubchiBeats/wedding-planner/zip/refs/heads/main$",
                           body)])


def test_a_small_project_is_read_from_one_archive_with_the_same_facts(monkeypatch, recent):
    raw = FakeNet(SUBCHI_FILES, refs=read("info_refs_subchibeats.pktline"))
    monkeypatch.setattr(source_fetch, "fetch_small", raw, raising=False)
    big = found_for(item())
    big["repo"]["size_kb"] = github.ZIP_MAX_REPO_KB + 1          # too big: file by file
    by_file = w.details(big, budget=budget())
    assert not [c for c in raw.calls if c["host"] == "codeload.github.com"]

    net = _zip_net(_zip(SUBCHI_FILES))
    monkeypatch.setattr(source_fetch, "fetch_small", net, raising=False)
    small = found_for(item())
    small["repo"]["size_kb"] = 300
    b = budget()
    from puffpai.catalogs import searchers as S

    def kept() -> int:
        return sum(os.path.getsize(os.path.join(S.cache_dir(), n))
                   for n in os.listdir(S.cache_dir()))

    before = kept()
    got = w.details(small, budget=b)
    hosts = [c["host"] for c in net.calls]
    assert hosts == ["codeload.github.com"]        # pinned by the archive: no github.com call
    zipped = next(c for c in net.calls if c["host"] == "codeload.github.com")
    assert zipped["service"] == "code_search" and zipped["key_id"] is None
    assert zipped["max_bytes"] == github.ZIP_MAX_BYTES
    for key in ("mode_guess", "integrable", "trust", "friendly"):
        assert got[key] == by_file[key], key
    assert got["details"] == by_file["details"]
    assert got["repo"]["license_read"] == "MIT" and got["repo"]["pinned_commit"] == SHA
    assert b.spent().get("github_zip") == 1 and "github_raw" not in b.spent()
    assert kept() - before < 4 * 1024                  # the archive itself is never kept on disk


def test_an_archive_that_cannot_be_read_falls_back_to_file_by_file(monkeypatch, recent):
    for body in (b"not a zip", _zip(SUBCHI_FILES, comment=b"not a commit"), b""):
        files = dict(SUBCHI_FILES)
        net = FakeNet(files, refs=read("info_refs_subchibeats.pktline"),
                      extra=[(r"codeload\.github\.com", body)] if body else [])
        monkeypatch.setattr(source_fetch, "fetch_small", net, raising=False)
        from puffpai.catalogs import searchers as S
        for n in os.listdir(S.cache_dir()) if os.path.isdir(S.cache_dir()) else []:
            os.remove(os.path.join(S.cache_dir(), n))
        small = found_for(item())
        small["repo"]["size_kb"] = 300
        got = w.details(small, budget=budget())
        assert got["repo"]["license_read"] == "MIT"
        assert net.raw_paths()[:2] == ["README.md", "LICENSE"]


def test_archive_paths_are_only_looked_up_never_trusted():
    import zipfile
    top = "p-" + SHA
    link = zipfile.ZipInfo(f"{top}/index.html")
    link.external_attr = (0o120777 << 16)
    body = bytearray(_zip({"README.md": b"# P\n", "big.js": b"x" * (github.FILE_MAX_BYTES + 1),
                           "../escape.txt": b"x", "a/../b.txt": b"x", "LICENSE": b"MIT"},
                          top=top, extra=[(link, b"../../secret")]))
    # Mark LICENSE encrypted in the central directory (the writer clears the flag).
    at = body.find(b"PK\x01\x02")
    while at >= 0:
        name_len = int.from_bytes(body[at + 28:at + 30], "little")
        if bytes(body[at + 46:at + 46 + name_len]) == f"{top}/LICENSE".encode():
            body[at + 8] |= 0x1
        at = body.find(b"PK\x01\x02", at + 4)
    body = bytes(body)
    reader = github._ZipReader.from_bytes(body, "o", "p", SHA)
    assert reader is not None and not reader.costly
    assert github._ZipReader.from_bytes(body, "o", "p", None).sha == SHA   # named by the archive
    assert reader.get("README.md") == b"# P\n"
    assert reader.get("index.html") is None                    # a link reads as missing
    assert reader.get("big.js") is None                        # over the file limit
    assert set(reader.members) == {"README.md", "big.js"}
    assert reader.tree_counts()["files"] == 2
    two_tops = _zip({"x": b"1"}, top="a", extra=[("b/y", b"2")])
    assert github._ZipReader.from_bytes(two_tops, "o", "p", SHA) is None
    assert github._ZipReader.from_bytes(b"PK\x05\x06" + b"\x00" * 18, "o", "p", SHA) is None


def test_an_archive_that_lies_about_a_file_size_is_read_with_the_limit():
    import io
    import zipfile
    top = "p-" + SHA
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"{top}/README.md", b"a" * (github.FILE_MAX_BYTES * 8))
    data = bytearray(buf.getvalue())
    # Rewrite the central directory's uncompressed size to claim 10 bytes.
    cd = data.rfind(b"PK\x01\x02")
    data[cd + 24:cd + 28] = (10).to_bytes(4, "little")
    reader = github._ZipReader.from_bytes(bytes(data), "o", "p", SHA)
    assert reader is not None
    got = reader.get("README.md")
    assert got is None or len(got) <= github.FILE_MAX_BYTES


def test_no_licence_names_are_asked_when_github_says_there_is_no_licence_file(monkeypatch):
    net = FakeNet({"README.md": b"# Tool\n", "index.html": b"<html></html>"},
                  refs=read("info_refs_subchibeats.pktline"))
    monkeypatch.setattr(source_fetch, "fetch_small", net, raising=False)
    it = dict(item(), license=None, size=github.ZIP_MAX_REPO_KB + 10)
    got = w.details(found_for(it), budget=budget())
    assert not set(net.raw_paths()) & set(github.LICENSE_NAMES)
    assert got["repo"]["license_read"] is None and got["trust"]["floor_ok"] is False


def test_a_large_project_is_probed_at_most_probes_max_times(monkeypatch, recent):
    net = FakeNet({"README.md": b"# Tool\n", "LICENSE": read("subchibeats_LICENSE"),
                   "Cargo.toml": b"[package]\nname = 't'\n"},
                  refs=read("info_refs_subchibeats.pktline"), listing=False)
    monkeypatch.setattr(source_fetch, "fetch_small", net, raising=False)
    it = dict(item(), size=github.ZIP_MAX_REPO_KB + 1, language=None)
    got = w.details(found_for(it), budget=budget())
    probes = [p for p in net.raw_paths() if p not in github.README_NAMES + github.LICENSE_NAMES]
    assert len(probes) == github.PROBES_MAX and "Cargo.toml" not in probes
    assert not set(probes) & set(github.PAGE_FOLDER_PROBES)      # skipped for a large project
    assert not [c for c in net.calls if c["host"] == "codeload.github.com"]
    assert got["mode_guess"] == "unknown" and got["integrable"] is False     # never a half guess


def test_with_a_listing_a_large_project_asks_only_for_files_that_are_there(monkeypatch, recent):
    """The listing of the top turns the blind
    probes into exact reads (fewer turns on the file host per project)."""
    net = FakeNet({"README.md": b"# Tool\n", "LICENSE": read("subchibeats_LICENSE"),
                   "Cargo.toml": b"[package]\nname = 't'\n"},
                  refs=read("info_refs_subchibeats.pktline"))
    monkeypatch.setattr(source_fetch, "fetch_small", net, raising=False)
    it = dict(item(), size=github.ZIP_MAX_REPO_KB + 1, language=None)
    got = w.details(found_for(it), budget=budget())
    assert net.raw_paths() == ["README.md", "LICENSE", "Cargo.toml"]
    assert got["repo"]["license_read"] == "MIT" and got["mode_guess"] != "unknown"


def test_probing_cut_short_by_the_budget_never_guesses_from_half_the_facts(monkeypatch, recent):
    page = read("subchibeats_index.html")
    net = FakeNet({"README.md": b"# Tool\n", "LICENSE": read("subchibeats_LICENSE"),
                   "docs/index.html": page, "Dockerfile": b"FROM node\n"},
                  refs=read("info_refs_subchibeats.pktline"), listing=False)
    monkeypatch.setattr(source_fetch, "fetch_small", net, raising=False)
    # README, then 3 probes (no listing: no licence name is asked)
    caps = dict(search_budget.CAPS_WIZARD, github_raw=4)
    it = dict(item(), size=100, language="JavaScript")     # small: its archive is missing here
    got = w.details(found_for(it), budget=budget(caps=caps))
    assert "docs/index.html" in net.raw_paths() and "Dockerfile" not in net.raw_paths()
    assert got["mode_guess"] == "unknown" and got["integrable"] is False
    assert got["trust"]["path"] != "static_small"


def test_archive_members_packed_another_way_are_never_read():
    import io
    import zipfile
    top = "p-" + SHA
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(f"{top}/README.md", b"# P\n", compress_type=zipfile.ZIP_DEFLATED)
        z.writestr(f"{top}/index.html", b"<html>" * 100, compress_type=zipfile.ZIP_BZIP2)
        z.writestr(f"{top}/app.js", b"x" * 100, compress_type=zipfile.ZIP_LZMA)
    reader = github._ZipReader.from_bytes(buf.getvalue(), "o", "p", SHA)
    assert set(reader.members) == {"README.md"}
    assert reader.get("index.html") is None and reader.get("app.js") is None
