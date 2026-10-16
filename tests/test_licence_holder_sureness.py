"""Licence holder sureness.

1. MAJOR: no word list decides a sure licence. A holder that is not exactly
   the owner's account or name, with nothing else added, is never sure and
   never pre-ticked: the licence's own holder words ("Individual Developers",
   "Community Others") and the owner's name with organisation, institution,
   company or group words, an address or a bracketed word after it
   ("SubchiBeats School", "Liam Chen Individual Devs") are read and marked
   to check. The 222-file real-licence corpus reads exactly as before (no
   refusal); 7 more of its licences are marked to check.
2. MAJOR: one licence reader. The build read its licence with its own list
   of patterns, "non-?commercial" first, so GPL-2.0, GPL-3.0 and the
   Unlicense read as personal use only and were refused after the download.
   ``integrate.fetch.read_license`` now reads with the plan's reader.
3. MINOR: "check this licence yourself" says what is unsure, in one line.
4. MINOR: a page's own script bytes are never shown as the size of an app.
5. MINOR: with two jobs or more, closer looks end earlier (the 90 s margin).

No network: every read is from text in the test or from a fixture.
"""
from __future__ import annotations

import copy
import json
import lzma
import os
import sys

import pytest

from puffpai.catalogs import trust_floor, where_to_look as w
from puffpai.catalogs.searchers import github
from puffpai.integrate import fetch

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import test_licence_custom_terms_bypass as T  # noqa: E402
from test_licence_custom_terms_bypass import recent  # noqa: E402,F401 - a fixture

PROBE = os.path.join(HERE, "fixtures", "recorded_web")
MIT_BODY = T.MIT_BODY


def mit(holder: str) -> str:
    return "MIT License\n\nCopyright (c) 2025 " + holder + "\n\n" + MIT_BODY


def probe(name: str) -> str:
    with open(os.path.join(PROBE, name), encoding="utf-8") as fh:
        return fh.read()


def corpus() -> list[dict]:
    with lzma.open(os.path.join(HERE, "fixtures", "real_licence_files.json.xz"), "rt",
                   encoding="utf-8") as fh:
        return json.load(fh)["rows"]


# ---------------------------------------------------------------------------
# 1. only the owner's account itself is a sure holder
# ---------------------------------------------------------------------------

SUBCHI = ("SubchiBeats", "wedding-planner")
LIAM = ("liamchen", "pantry-helper")

#: The validator's probes on b0d1ab9 that read as sure MIT (q1, q2, test_q_e2e).
NEVER_SURE = [
    ("Individual Developers", SUBCHI, "general"),
    ("Individual Developers Inc.", SUBCHI, "general"),
    ("Individual Developers and Others", SUBCHI, "general"),
    ("Community Others", SUBCHI, "general"),
    ("Individual Contributors", (), "general"),
    ("Individual Authors", ("indiedevs", "x"), "general"),
    ("Others", (), "general"),
    ("De La Community", (), "general"),
    ("The Individual Developers", (), "general"),
    ("Individual Project Contributors", (), "general"),
    ("SubchiBeats Individual Developers", SUBCHI, "added"),
    ("SubchiBeats Open Source Projects", SUBCHI, "added"),
    ("SubchiBeats School", SUBCHI, "added"),
    ("Liam Chen Individual Devs", LIAM, "added"),
    ("Liam Chen Individual Developers", LIAM, "added"),
    ("Liam Chen and Individual Developers", LIAM, "general"),
    ("Liam Chen, Individual Developers", LIAM, "general"),
    ("Liam Chen School", LIAM, "added"),
    ("Liam Chen Open Source", LIAM, "added"),
    ("Liam Chen Open Source Projects", LIAM, "added"),
    ("Liam Chen Community Projects", LIAM, "added"),
    ("Liam Chen Others", LIAM, "added"),
    ("Liam Chen Individual", LIAM, "added"),
    ("Liam Chen AG", LIAM, "added"),
    ("Liam Chen KK", LIAM, "added"),
    ("Liam Chen Co. St.", LIAM, "added"),
    ("Liam Chen (LC)", LIAM, "added"),
    ("Liam Chen <https://liamchen.dev/about>", LIAM, "address"),
    ("Liam Chen <liam@chen.example>", LIAM, "address"),
    ("Inc. <https://liamchen.dev>", LIAM, "address"),
    ("Indie Developers", ("indiedevs", "x"), "added"),
    ("Individual Developers", ("acme", "individual-devs"), "general"),   # the repository never vouches
    ("Waldo Jaquith", ("waldoj", "frostline"), "added"),                   # an initial spelled out
    ("Sahib Singh", SUBCHI, "stranger"),
    # A piece cut out of the account is not the account (any four letters
    # inside it used to be the owner).
    ("Anna", ("joannasmith", "x"), "added"),
    ("Smith", ("joannasmith", "x"), "added"),
    ("csxiaoyao", ("csxiaoyaojianxian", "x"), "added"),
]


@pytest.mark.parametrize("holder, identity, why", NEVER_SURE)
def test_a_holder_that_is_not_exactly_the_owner_is_never_sure(holder, identity, why):
    text = mit(holder)
    assert w.license_from_text(text, identity=identity) == "MIT"        # read, never refused
    assert w.licence_to_check(text, identity=identity) is True
    assert w.licence_check_why(text, identity=identity) == why


@pytest.mark.parametrize("holder, identity", [
    ("Liam Chen", ("liamchen", "x")), ("Liam Chen", ("LiamChen", "x")),
    ("Liam Chen", ("liam-chen", "y")), ("SubchiBeats", SUBCHI), ("sindresorhus", ("sindresorhus", "got")),
    ("Sindre Sorhus", ("sindresorhus", "got")), ("Matheus Baldissara", ("BaldissaraMatheus", "t")),
    ("Anton Brink", ("AntonBrinkCodes", "c")), ("Home Assistant", ("home-assistant", "core")),
    ("Bu Mai", ("bumai", "tool")), ("Joanna Smith", ("joannasmith", "x")),
    ("Akshita Gupta", ("akshitagupta15june", "PetMe")), ("Paulo Brandão", ("paulobrandaodev", "g")),
])
def test_the_owners_account_word_for_word_is_sure(holder, identity):
    text = mit(holder)
    assert w.license_from_text(text, identity=identity) == "MIT"
    assert w.licence_to_check(text, identity=identity) is False
    assert w.licence_check_why(text, identity=identity) == ""


def test_no_holder_at_all_is_sure():
    text = "MIT License\n\nCopyright (c) 2025\n\n" + MIT_BODY
    assert w.license_from_text(text, identity=SUBCHI) == "MIT"
    assert w.licence_to_check(text, identity=SUBCHI) is False


def test_a_condition_in_the_owners_own_name_is_marked():
    text = mit("Personal Capital")
    ident = ("personalcapital", "tools")
    assert w.licence_to_check(text, identity=ident) is True
    assert w.licence_check_why(text, identity=ident) == "condition"


@pytest.mark.parametrize("holder", [
    "Individual Developers", "Individual Developers Inc.", "Individual Developers and Others",
    "Community Others", "SubchiBeats Individual Developers", "SubchiBeats Open Source Projects",
    "SubchiBeats School", "Sahib Singh",
])
def test_end_to_end_none_of_them_is_pre_ticked(monkeypatch, recent, holder):  # noqa: F811
    """The validator's test_q_e2e: the archive read at the pin, the trust
    floor with 5,000 stars, and the plan's words."""
    from puffpai.wizard import find_parts
    text = T.MIT_LICENSE.replace("Sahib Singh", holder)
    got = T._from_archive(monkeypatch, dict(T.D.SUBCHI_FILES, LICENSE=text.encode()))
    rec = copy.deepcopy(got)
    rec["repo"]["stars"] = 5000
    assert rec["repo"]["license_read"] == "MIT" and rec["repo"]["license_check"] is True
    ok, path, reasons = trust_floor.check(rec)
    assert ok and path == "stars" and trust_floor.CHECK_LICENCE in reasons
    assert trust_floor.pretick_allowed(rec) is False
    words = find_parts._licence_words(rec)[1]
    assert words.startswith("Free to use and change (MIT), check this licence yourself: ")
    assert words.endswith(".") and words.count(".") == 1 and "\n" not in words
    assert trust_floor.licence_check_words(rec) in words


def test_the_owners_own_line_is_still_pre_ticked(monkeypatch, recent):  # noqa: F811
    text = T.MIT_LICENSE.replace("Sahib Singh", "SubchiBeats")
    got = T._from_archive(monkeypatch, dict(T.D.SUBCHI_FILES, LICENSE=text.encode()))
    rec = copy.deepcopy(got)
    rec["repo"]["stars"] = 5000
    assert rec["repo"]["license_check"] is False and rec["repo"]["license_check_why"] == ""
    assert trust_floor.pretick_allowed(rec) is True


def test_the_222_file_corpus_no_refusal_and_a_reason_for_every_mark():
    """Same reads as before (the base test pins 0 lost, 0 changed); every
    licence marked to check has a reason from its own holder lines."""
    rows = corpus()
    whys = {}
    for r in rows:
        ident = (r["owner"], r["name"])
        if not w.license_from_text(r["text"], identity=ident):
            continue
        check = w.licence_to_check(r["text"], identity=ident)
        why = w.licence_check_why(r["text"], identity=ident)
        assert bool(why) is check, r["key"]
        if check:
            whys[r["key"]] = why
    # 64 of the base's 194, plus worship-works; it also reads two more
    # real BSD-2-Clause files ("All rights reserved." on its own line), both
    # marked: babybuddy ("Baby Buddy's Contributors") and stretchly ("Jan
    # Hovancik" of hovancik), "added".
    assert len(whys) == 67
    assert whys["babybuddy/babybuddy"] == "added" and whys["hovancik/stretchly"] == "added"
    assert whys["csxiaoyaojianxian/JavaScriptStudy"] == "added"   # a piece of the account
    assert "unsure" not in whys.values()
    assert whys["SubchiBeats/wedding-planner"] == "stranger"
    assert whys["uber/react-vis"] == "added" and whys["commaai/opendbc"] == "address"
    assert whys["blindpassasjer/gigboy"] == "stranger"     # Apache's owner line, filled in


# ---------------------------------------------------------------------------
# 3. what to check, in plain words
# ---------------------------------------------------------------------------

def test_every_reason_has_plain_words():
    assert set(trust_floor.CHECK_WHY_WORDS) == set(w.CHECK_WHY)
    for words in trust_floor.CHECK_WHY_WORDS.values():
        assert words and words == words.strip() and not words.endswith(".")
        assert "—" not in words and "door" not in words.lower()
        assert len(words) <= 60


def test_an_unknown_reason_says_unsure_and_a_sure_licence_says_nothing():
    found = {"source": "github", "repo": {"license_read": "MIT", "pinned_commit": "a" * 40,
                                          "license_check": True, "license_check_why": "???"}}
    assert trust_floor.licence_check_words(found) == trust_floor.CHECK_WHY_WORDS["unsure"]
    found["repo"]["license_check"] = False
    assert trust_floor.licence_check_words(found) == ""


def test_the_reason_travels_with_the_find():
    from puffpai.catalogs import searchers
    assert "license_check_why" in searchers._REPO_KEYS


# ---------------------------------------------------------------------------
# 2. one licence reader: the build reads as the plan reads
# ---------------------------------------------------------------------------

def _tree(tmp_path, files: dict) -> str:
    root = tmp_path / "src"
    root.mkdir(parents=True)
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text if isinstance(text, bytes) else text.encode("utf-8"))
    return str(root)


@pytest.mark.parametrize("name, family, is_open", [
    ("license_GPL-2.0.txt", "GPL-2.0", True), ("license_GPL-2.0_github.txt", "GPL-2.0", True),
    ("license_GPL-3.0.txt", "GPL-3.0", True), ("license_GPL-3.0_github.txt", "GPL-3.0", True),
    ("license_Unlicense.txt", "Unlicense", True), ("license_AGPL-3.0.txt", "AGPL-3.0", True),
    ("license_LGPL-2.1.txt", "LGPL", True), ("license_MPL-2.0.txt", "MPL-2.0", True),
    ("license_Apache-2.0.txt", "Apache-2.0", True), ("license_CC0-1.0.txt", "CC0-1.0", True),
    ("license_CC-BY-4.0.txt", "CC-BY-4.0", True), ("license_ISC.txt", "ISC", True),
    ("license_0BSD.txt", "0BSD", True), ("license_BSD-3-Clause.txt", "BSD-3-Clause", True),
    ("license_CC-BY-NC-4.0.txt", "CC-BY-NC", False),
    ("license_CC-BY-ND-4.0.txt", "custom", False),            # the old reader said CC0
    ("license_none_isc_plus_logo_note.txt", "custom", False), # the old reader said CC BY-SA
])
def test_the_build_reads_common_licences_right(tmp_path, name, family, is_open):
    got = fetch.read_license(_tree(tmp_path, {"LICENSE": probe(name)}))
    assert got["family"] == family and got["open"] is is_open and got["file"] == "LICENSE"


def test_the_e2e_victims_are_free_now(tmp_path):
    """exercises.json (Unlicense) and four GPL apps are not read as personal
    use only (the GPL texts say "noncommercially", the Unlicense "commercial
    or non-commercial")."""
    for name in ("license_Unlicense.txt", "license_GPL-2.0.txt", "license_GPL-3.0.txt"):
        assert "commercial" in probe(name).lower()
        root = _tree(tmp_path / name, {"LICENSE": probe(name)})
        got = fetch.read_license(root)
        assert got["open"] and not got["nc"], name


def test_the_build_and_the_plan_read_every_corpus_file_alike(tmp_path):
    """One reader: all 222 real licences and the probe texts read the same at
    build time as in the plan, with the project's own names."""
    rows = corpus() + [{"key": n, "owner": "", "name": "", "text": probe(n)}
                       for n in sorted(os.listdir(PROBE))
                       if n.startswith("license_") and n.endswith(".txt")]
    root = tmp_path / "t"
    root.mkdir()
    opened = 0
    for r in rows:
        (root / "LICENSE").write_text(r["text"], encoding="utf-8", newline="")
        got = fetch.read_license(str(root), identity=(r["owner"], r["name"]))
        plan = w.license_from_text(r["text"], identity=(r["owner"], r["name"]))
        want = fetch.family_of(plan) if plan else "custom"
        assert got["family"] == (want if want in fetch.FAMILIES else "custom"), r["key"]
        opened += bool(got["open"])
    assert opened >= 190


def test_no_licence_file_is_none_and_a_notice_with_terms_is_custom(tmp_path):
    assert fetch.read_license(_tree(tmp_path / "a", {"README.md": "hi"}))["family"] == "none"
    root = _tree(tmp_path / "b", {"LICENSE": T.MIT_LICENSE,
                                  "NOTICE": "Companies must pay for a licence to use this.\n"})
    assert fetch.read_license(root)["family"] == "custom"
    root = _tree(tmp_path / "c", {"LICENSE": T.MIT_LICENSE,
                                  "LICENSE-COMMERCIAL.md": "Commercial use needs a paid licence.\n"})
    assert fetch.read_license(root)["family"] == "custom"


def test_a_licence_file_too_big_to_read_is_custom(tmp_path):
    big = T.MIT_LICENSE + "\n" + ("x" * (github.FILE_MAX_BYTES + 10))
    assert fetch.read_license(_tree(tmp_path, {"LICENSE": big}))["family"] == "custom"


def test_a_linked_licence_file_is_never_followed(tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text(T.MIT_LICENSE, encoding="utf-8")
    root = tmp_path / "src"
    root.mkdir()
    try:
        os.symlink(str(outside), str(root / "LICENSE"))
    except (OSError, NotImplementedError):
        pytest.skip("this computer cannot make a link without more rights")
    got = fetch.read_license(str(root))
    assert got["family"] == "custom" and not got["open"]


class _FakeEntry:
    def __init__(self, name, path, *, link=False, plain=True, folder=False):
        self.name, self.path = name, path
        self._link, self._plain, self._folder = link, plain, folder

    def is_symlink(self):
        return self._link

    def is_file(self, follow_symlinks=True):
        return self._plain and not (self._link and not follow_symlinks)

    def is_dir(self, follow_symlinks=True):
        return self._folder


def test_a_link_or_odd_file_named_like_a_licence_is_custom_on_any_computer(tmp_path, monkeypatch):
    """The same rule as the link test, without making a link (Windows needs
    more rights for that): the reader is handed an entry that is a link, and
    one that is neither a file nor a folder."""
    root = _tree(tmp_path, {"README.md": "hi"})
    real = os.path.join(root, "README.md")
    for entry in (_FakeEntry("LICENSE", real, link=True),
                  _FakeEntry("COPYING", real, plain=False)):
        monkeypatch.setattr(os, "scandir", lambda _p, e=entry: iter([e]))
        got = github.read_licence_tree(root)
        assert got["read"] == w.CUSTOM_LICENSE and got["file"] == entry.name
        monkeypatch.undo()
    assert fetch.read_license(root)["family"] == "none"


def test_the_build_reads_with_the_finds_own_names(tmp_path, monkeypatch):
    seen = []
    real = github.read_licence_tree

    def spy(root, owner="", name=""):
        seen.append((owner, name))
        return real(root, owner, name)

    monkeypatch.setattr(github, "read_licence_tree", spy)
    rec = {"from": {"url": "https://github.com/SubchiBeats/wedding-planner"}}
    assert fetch.identity_of(rec) == SUBCHI
    assert fetch.identity_of({"from": {"url": "https://evil.example/SubchiBeats/x"}}) == ("", "")
    fetch.read_license(_tree(tmp_path, {"LICENSE": T.MIT_LICENSE}), identity=fetch.identity_of(rec))
    assert seen == [SUBCHI]


def test_a_reader_that_cannot_run_confirms_nothing(tmp_path, monkeypatch):
    def broken(*_a, **_k):
        raise RuntimeError("no reader")

    monkeypatch.setattr(github, "read_licence_tree", broken)
    got = fetch.read_license(_tree(tmp_path, {"LICENSE": T.MIT_LICENSE}))
    assert got["family"] == "custom" and not got["open"]


def test_the_build_has_no_pattern_list_of_its_own():
    assert not hasattr(fetch, "_TEXT_RULES")


# ---------------------------------------------------------------------------
# 4 and 5. size, and the time margin
# ---------------------------------------------------------------------------

def test_a_pages_own_script_bytes_are_not_the_apps_size():
    from puffpai.wizard import find_parts
    found = {"repo": {"size_kb": None}, "details": {"code_bytes": 900}}
    assert find_parts._size(found) is None
    found["repo"]["size_kb"] = 2048
    assert find_parts._size(found) == 2048 * 1024
    found["repo"]["size_kb"] = 0                    # empty, or not measured yet
    assert find_parts._size(found) is None and find_parts._size_words(None) == ""


def test_a_listed_find_never_measured_shows_no_size_line():
    """myfin and chartdb on b0d1ab9: the self-hosting list gives no size and
    their empty details carry code_bytes 0; the plan said "About 1 KB"."""
    from puffpai.catalogs import searchers
    from puffpai.wizard import find_parts
    found = {"source": "awesome_selfhosted", "repo": {"size_kb": None},
             "details": searchers.empty_details()}
    assert found["details"]["code_bytes"] == 0
    assert find_parts._size(found) is None
    assert "KB" not in find_parts._size_words(find_parts._size(found))


def test_closer_looks_end_earlier_with_more_than_one_job():
    from puffpai.wizard import find_parts
    assert find_parts._look_until(90.0, [{"id": "j1"}]) == 90.0 - find_parts.SCORING_S
    assert find_parts._look_until(90.0, []) == 90.0 - find_parts.SCORING_S
    two = [{"id": "j1"}, {"id": "j2"}]
    assert find_parts._look_until(90.0, two) == 90.0 - find_parts.SCORING_MULTI_S
    assert find_parts.SCORING_MULTI_S >= find_parts.SCORING_S + 5


# ---------------------------------------------------------------------------
# 4b. a list never makes a licence BuildBot read itself free
# ---------------------------------------------------------------------------

def _listed_find(read):
    return {"source": "awesome_selfhosted", "part": "code-app", "name": "MyFin Budget",
            "url": "https://github.com/afaneca/myfin",
            "repo": {"stars": 3, "pushed_at": "2026-09-12", "license_reported": "GPL-3.0",
                     "license_read": read, "pinned_commit": "a" * 40}}


@pytest.mark.parametrize("read", [w.CUSTOM_LICENSE, "CC-BY-NC-4.0"])
def test_a_listed_find_whose_own_read_is_not_open_is_never_passed(monkeypatch, recent, read):  # noqa: F811
    """Acceptance (v_budget): MyFin read as custom at its pin, yet passed on
    the self-hosting list's GPL-3.0 and was pre-ticked."""
    monkeypatch.setattr(trust_floor, "listed_lookup", lambda _u: {"license": "GPL-3.0"})
    found = _listed_find(read)
    ok, path, reasons = trust_floor.check(found)
    assert not ok and path == "" and reasons == ["license"]
    assert trust_floor.pretick_allowed(found) is False


@pytest.mark.parametrize("read", [None, "GPL-3.0"])
def test_a_listed_find_with_no_read_or_an_open_read_still_passes(monkeypatch, recent, read):  # noqa: F811
    monkeypatch.setattr(trust_floor, "listed_lookup", lambda _u: {"license": "GPL-3.0"})
    ok, path, _ = trust_floor.check(_listed_find(read))
    assert ok and path == "listed"


# ---------------------------------------------------------------------------
# 6. recall: finds GitHub reports with no licence are read at the pin
# ---------------------------------------------------------------------------

def _gh(fid, job, fit, *, reported=None, size=200, stars=12, read=None, **repo):
    rec = {"id": fid, "source": "github", "part": "code-app",
           "url": "https://github.com/o/" + fid,
           "repo": dict({"host": "github.com", "owner": "o", "name": fid, "stars": stars,
                         "pushed_at": "2026-09-01", "license_reported": reported,
                         "license_read": read, "size_kb": size}, **repo)}
    row = dict(rec, meaning={"job": job, "blurb": fit, "fit": fit, "state": "dropped",
                             "why_dropped": "more than enough like it"})
    return rec, row


def _pick(rows, done=()):
    from puffpai.wizard import find_parts
    found = {r[0]["id"]: r[0] for r in rows}
    return [f["id"] for f in find_parts._unreported_licence_looks([r[1] for r in rows], found,
                                                                  done=set(done))]


def test_the_best_unreported_finds_of_every_job_are_read_in_turns(recent):  # noqa: F811
    rows = [_gh("a1", "j1", 0.70), _gh("a2", "j1", 0.66), _gh("a3", "j1", 0.65),
            _gh("b1", "j2", 0.60), _gh("b2", "j2", 0.59)]
    assert _pick(rows) == ["a1", "b1", "a2", "b2"]           # 2 per job, best first, in turns
    assert _pick(rows, done={"a1"}) == ["a2", "b1", "a3", "b2"]


def test_at_most_ten_unreported_looks_in_all(recent):  # noqa: F811
    from puffpai.wizard import find_parts
    rows = [_gh(f"x{j}{n}", f"j{j}", 0.7 - n / 100) for j in range(8) for n in range(3)]
    got = _pick(rows)
    assert len(got) == find_parts.UNREPORTED_LOOKS_MAX == 10


@pytest.mark.parametrize("kw", [
    {"reported": "MIT"},                       # GitHub named one: the usual looks read it
    {"reported": "NOASSERTION"},
    {"read": "MIT"},                           # already read
    {"size": 900_000},                         # too big for one archive: no licence read
    {"size": None},
    {"archived": True}, {"fork": True},
    {"stars": 0, "pushed_at": "2019-01-01"},   # no look can carry it past the floor
])
def test_these_are_never_an_unreported_look(recent, kw):  # noqa: F811
    repo_kw = {k: v for k, v in kw.items() if k not in ("reported", "size", "stars", "read")}
    rec, row = _gh("p", "j1", 0.7, reported=kw.get("reported"), size=kw.get("size", 200),
                   stars=kw.get("stars", 12), read=kw.get("read"), **repo_kw)
    assert _pick([(rec, row)]) == []


def test_a_find_that_does_not_fit_is_never_an_unreported_look(recent):  # noqa: F811
    rec, row = _gh("p", "j1", 0.1)
    assert _pick([(rec, row)]) == []
    rec, row = _gh("q", "j1", 0.7)
    row["meaning"]["why_dropped"] = "page"
    assert _pick([(rec, row)]) == []
    rec, row = _gh("r", None, 0.7)
    assert _pick([(rec, row)]) == []


def test_plan_items_reads_them_after_every_other_look_and_by_the_same_end():
    import inspect
    from puffpai.wizard import find_parts
    src = inspect.getsource(find_parts.plan_items)
    call = src.index("_unreported_licence_looks(")
    assert src.index("many(closer, budget=budget, deadline=look_until") < call
    after = src[call:call + 700]
    assert "deadline=look_until" in after and "time.monotonic() < look_until" in after


def test_an_unreported_licence_is_read_from_the_archive_never_guessed(monkeypatch, recent):  # noqa: F811
    """GitHub said "no licence"; the archive at the pin has an MIT file the
    plan's reader knows: it reads MIT. With no licence file it stays none."""
    got = T._from_archive(monkeypatch, dict(T.D.SUBCHI_FILES), license=None)
    assert got["repo"]["license_reported"] is None and got["repo"]["license_read"] == "MIT"
    files = {k: v for k, v in T.D.SUBCHI_FILES.items() if k != "LICENSE"}
    got = T._from_archive(monkeypatch, files, license=None)
    assert got["repo"]["license_read"] is None
    assert trust_floor.check(got)[0] is False
