"""Web apps that must be built are built only in the locked box.

``npm ci --ignore-scripts`` from the project's own recorded lock, then its
``build`` script, each a separate box run with no shell; npm's registry is the
only host the install may reach and the build reaches none; the built files
are exported and added like a static app. No box (or not ready): it
is built and run on this computer
after her yes (stage ``host``), never left waiting for the parked box; an item
that waited before (status ``waiting_box``) still finishes with Finish. A lock
naming parts from anywhere else waits for her yes.
No lock: left out. A build that fails: left out, its box project removed.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import sys
import types

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "fixtures", "integrate"))
import integrate_support as sup  # noqa: E402

from puffpai.integrate import apply, records, wording  # noqa: E402

ITEM = {"id": "apps_code:github:someone/party-app", "label": "Party app"}


def lock(resolved="https://registry.npmjs.org/react/-/react-18.2.0.tgz"):
    return json.dumps({"name": "party-app", "lockfileVersion": 3, "packages": {
        "": {"name": "party-app"},
        "node_modules/react": {"version": "18.2.0", "resolved": resolved,
                               "integrity": "sha512-" + "A" * 86 + "=="}}})


def build_tree(root, *, lock_text=None, shrinkwrap_text=None):
    sup.write(root, "LICENSE", sup.MIT_TEXT)
    sup.write(root, "README.md", "# Party app\n\nPlans a party.\n")
    sup.write(root, "package.json", json.dumps({"name": "party-app",
                                                "scripts": {"build": "vite build"}}))
    if lock_text is not False:
        sup.write(root, "package-lock.json", lock_text or lock())
    if shrinkwrap_text is not None:
        sup.write(root, "npm-shrinkwrap.json", shrinkwrap_text)
    sup.write(root, "index.html", '<div id="root"></div><script type="module" src="/src/main.tsx"></script>')
    sup.write(root, "src/main.tsx", "export const App = () => null;\n" * 800)
    return root


#: The box's id rule for run ids and projects (``box.driver.ID_RE``).
BOX_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}\Z")


class Box:
    """A fake ``puffpai.box.driver`` that keeps the box's pinned contract:
    ``add_waiting`` refuses an entry without ``item.id`` and ``item.apply.kind``;
    Finish looks the applier up by ``item.apply.kind`` (an unknown kind fails
    "not in this build"); ``run`` refuses a run id or project outside the id
    rule."""

    def __init__(self, state="ready", build_ok=True, finishes=True):
        self.state = state
        self.build_ok = build_ok
        self.finishes = finishes
        self.runs, self.puts, self.removed, self.waiting_items = [], [], [], []
        self.appliers = {}
        self.out_dir = "dist"

    def module(self):
        mod = types.ModuleType("puffpai.box.driver")
        mod.status = lambda: {"state": self.state, "message": "Windows' Linux feature is off."}
        mod.put_project = self.put_project
        mod.run = self.run
        mod.export = self.export
        mod.remove_project = lambda p: self.removed.append(p)
        mod.add_waiting = self.add_waiting
        mod.waiting = lambda: list(self.waiting_items)
        mod.new_run_id = lambda: "r" + secrets.token_hex(8)
        if self.finishes:
            mod.register_waiting_applier = self.appliers.__setitem__
        return mod

    def add_waiting(self, item):
        entry = dict(item) if isinstance(item.get("item"), dict) else {"item": dict(item)}
        line = entry["item"]
        kind = str((line.get("apply") or {}).get("kind") or "")
        if not re.fullmatch(r"[a-z][a-z0-9_.]{0,39}", kind) or not str(line.get("id") or ""):
            raise ValueError("a waiting item needs an id and an apply kind")
        self.waiting_items = [w for w in self.waiting_items if w["item"]["id"] != line["id"]]
        self.waiting_items.append(entry)

    def finish(self):
        """The box's ``finish_waiting`` + ``_apply_waiting``, box ready."""
        out, left = [], []
        for entry in self.waiting_items:
            fn = self.appliers.get(entry["item"]["apply"]["kind"])
            got = fn(entry, lambda _l: None) if fn else {"status": "failed",
                                                         "line": "not in this build"}
            out.append(got)
            if got.get("status") == "waiting":
                left.append(entry)
        self.waiting_items = left
        return out

    def put_project(self, project, tar_path, disk_mb=4096):
        import tarfile
        assert BOX_ID.match(project)
        with tarfile.open(tar_path) as tar:
            names = tar.getnames()
            assert all(m.isfile() for m in tar.getmembers())
        self.puts.append((project, names))
        return {"ok": True, "exit_code": 0}

    def run(self, req):
        if not (BOX_ID.match(req["run_id"]) and BOX_ID.match(req["project"])):
            return {"ok": False, "refused": "bad_run_id"}
        self.runs.append(req)
        ok = self.build_ok or req["argv"][:2] != ["npm", "run"]
        return {"ok": ok, "exit_code": 0 if ok else 1}

    def export(self, project, rel_path, dest_dir, *, max_bytes=200_000_000):
        if os.path.isdir(dest_dir) and os.listdir(dest_dir):
            raise RuntimeError("not_empty")
        if rel_path != self.out_dir:
            os.makedirs(os.path.join(dest_dir, "partial"), exist_ok=True)
            raise FileNotFoundError(rel_path)
        path = sup.write(dest_dir, "index.html",
                         "<html><body><form><input><button>Add</button></form>"
                         "<script src='assets/app.js'></script></body></html>")
        sup.write(dest_dir, "assets/app.js", "localStorage.setItem('a','b');")
        return [path]


@pytest.fixture
def world(monkeypatch, tmp_path):
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(tmp_path / "state"))
    sup.fresh_drawbridge(monkeypatch)
    from puffpai.security import consent_tickets
    consent_tickets.reset()
    from puffpai.integrate import run_here
    monkeypatch.setattr(run_here, "missing_programs", lambda plan: [])   # Node.js is here

    def make(*, box=None, friendly="Party app", **tree_kw):
        found = sup.found(name="party-app", friendly=friendly, mode="web_build",
                          what="Plans a party.")
        tree = build_tree(str(tmp_path / "tree"), **tree_kw)
        archive = sup.tar_gz_of(tree)
        found["repo"]["size_kb"] = len(archive) // 1024 + 1
        h = sup.install_fakes(monkeypatch, str(tmp_path / "apps"), founds={ITEM["id"]: found},
                              archives={sup.archive_url(found): archive})
        if box is not None:
            monkeypatch.setitem(sys.modules, "puffpai.box.driver", box.module())
        h["box"] = box
        return h
    return make


def test_it_is_built_in_the_box_and_added(world):
    h = world(box=Box())
    ctx = sup.Ctx()
    out = apply.apply_code_project(ITEM, ctx)
    assert out["status"] == "added", ctx.lines
    box = h["box"]
    rec = records.get(out["integration_id"])
    assert box.puts and box.puts[0][0] == rec["id"]
    assert "node_modules" not in " ".join(box.puts[0][1])
    first, second = box.runs
    assert first["argv"][:3] == ["npm", "ci", "--ignore-scripts"]
    assert first["allow_hosts"] == ["registry.npmjs.org"]
    assert second["argv"][:3] == ["npm", "run", "build"] and second["allow_hosts"] == []
    assert all(isinstance(a, str) for r in box.runs for a in r["argv"])
    assert first["project"] == rec["id"] and first["service"].startswith("box_")
    assert rec["lock"]["kind"] == "npm" and len(rec["lock"]["sha256"]) == 64
    web = h["webapps"].calls[0]["files_dir"]
    assert web.replace("\\", "/").endswith(f"{rec['id']}/web")
    assert wording.BUILDING_IN_BOX in ctx.lines


def _waiting_from_before(box) -> dict:
    """An item as the old build left it: waiting for the parked box."""
    out = apply.apply_code_project(ITEM, sup.Ctx())
    rec = records.get(out["integration_id"])
    rec["status"] = "waiting_box"
    rec["held"] = {"reasons": ["box_needed"], "ticket_target": ""}
    records.save(rec)
    box.add_waiting({"item": {"id": rec["id"], "label": "Party app",
                              "apply": {"kind": "code_project"}, "integration": rec["id"]}})
    return out


def test_without_the_box_it_waits_for_a_yes_to_run_here(world):
    h = world(box=Box(state="no_wsl"))
    out = apply.apply_code_project(ITEM, sup.Ctx())
    rec = records.get(out["integration_id"])
    assert rec["status"] == "held" and out["status"] == "held"
    assert rec["held"]["reasons"] == ["host_run"] and rec["held"]["ticket_target"]
    assert h["box"].waiting_items == []                 # never parked for the box
    assert h["box"].runs == [] and h["webapps"].calls == []


def test_finish_continues_a_waiting_item(world):
    h = world(box=Box(state="no_wsl"))
    out = _waiting_from_before(h["box"])
    assert records.get(out["integration_id"])["status"] == "waiting_box"
    h["box"].state = "ready"
    got = apply.resume_waiting(out["integration_id"])
    assert got["status"] == "added", got
    assert records.get(out["integration_id"])["status"] == "ready"
    assert apply.resume_waiting(out["integration_id"])["status"] == "failed"   # not waiting now


def test_the_box_finish_button_reaches_it(world, monkeypatch):
    """connect() registers the Finish applier with the box; the box finds it
    by the entry's apply kind and the item is added."""
    box = Box(state="no_wsl")
    h = world(box=box)
    from puffpai import add_more, build_abilities
    from puffpai.integrate import actions
    sup.fresh_drawbridge(monkeypatch, register_code_project=False)
    for mod, name in ((actions, "register_source"), (add_more, "register_applier"),
                      (add_more, "register_remover"), (build_abilities, "register_provider")):
        monkeypatch.setattr(mod, name, lambda *a, **k: None)
    done = apply.connect()
    assert "box_waiting:code_project" in done
    assert box.appliers["code_project"] is apply.finish_waiting_item
    out = _waiting_from_before(box)
    box.state = "ready"
    results = box.finish()
    assert [r["status"] for r in results] == ["added"], results
    assert records.get(out["integration_id"])["status"] == "ready"
    assert box.waiting_items == [] and len(h["webapps"].calls) == 1


def test_finish_while_the_box_is_still_not_ready_asks_to_run_it_here(world):
    """An item left waiting by the old build is never stuck: with the box
    still parked, Finish turns it into the yes to run it on this computer."""
    box = Box(state="no_wsl")
    world(box=box)
    box.appliers["code_project"] = apply.finish_waiting_item
    out = _waiting_from_before(box)
    assert [r["status"] for r in box.finish()] == ["held"]
    assert box.waiting_items == []
    rec = records.get(out["integration_id"])
    assert rec["status"] == "held" and rec["held"]["reasons"] == ["host_run"]


def test_finish_reads_only_the_record(world):
    """A waiting entry pointing somewhere else (or at a record that is not
    waiting) finishes nothing."""
    box = Box(state="ready")
    world(box=box)
    for entry in ({"item": {"id": "x", "apply": {"kind": "code_project"},
                            "integration": "../../etc"}},
                  {"item": {"id": "x", "apply": {"kind": "code_project"},
                            "integration": "nothing-here-abcdefgh"}}, {}):
        got = apply.finish_waiting_item(entry, lambda _l: None)
        assert got["status"] == "failed", got
    assert box.runs == []


def test_a_box_that_cannot_finish_things_holds_instead(world):
    """Never waiting forever: held (for the yes to run it here)."""
    h = world(box=Box(state="no_wsl", finishes=False))
    out = apply.apply_code_project(ITEM, sup.Ctx())
    rec = records.get(out["integration_id"])
    assert rec["status"] == "held" and h["box"].waiting_items == []


def test_run_ids_follow_the_box_rule_for_the_longest_names(world):
    box = Box()
    h = world(box=box, friendly="A really quite long party planning app name")
    out = apply.apply_code_project(ITEM, sup.Ctx())
    rec = records.get(out["integration_id"])
    assert len(rec["id"]) == 39, rec["id"]
    assert out["status"] == "added", out
    assert len(box.runs) == 2 and all(BOX_ID.match(r["run_id"]) for r in box.runs)
    assert len({r["run_id"] for r in box.runs}) == 2
    assert len(h["webapps"].calls) == 1


def test_without_the_box_module_it_is_held_for_a_yes_to_run_here(world, monkeypatch):
    world()
    monkeypatch.setitem(sys.modules, "puffpai.box.driver", None)
    out = apply.apply_code_project(ITEM, sup.Ctx())
    assert out["status"] == "held"
    assert records.get(out["integration_id"])["held"]["reasons"] == ["host_run"]
    assert "locked box" not in out["line"]


def test_a_lock_that_reaches_elsewhere_waits_for_a_yes(world):
    h = world(box=Box(), lock_text=lock("git+https://example.com/evil.git"))
    out = apply.apply_code_project(ITEM, sup.Ctx())
    rec = records.get(out["integration_id"])
    assert rec["status"] == "held" and rec["held"]["reasons"] == ["lock_offsite"]
    assert rec["held"]["ticket_target"].startswith(f"held:{rec['id']}:")
    assert h["box"].runs == []


def test_no_lock_no_build(world):
    h = world(box=Box(), lock_text=False)
    out = apply.apply_code_project(ITEM, sup.Ctx())
    assert out["status"] in ("held", "refused")
    assert h["box"].runs == [] and h["webapps"].calls == []


def test_a_build_that_fails_is_left_out(world):
    h = world(box=Box(build_ok=False))
    ctx = sup.Ctx()
    out = apply.apply_code_project(ITEM, ctx)
    rec = records.get(out["integration_id"])
    assert out["status"] == "failed" and rec["status"] == "failed"
    # The line says which step failed (never "a mistake
    # in its own code" for every step).
    assert apply.build_failed_line("build") in ctx.lines
    assert h["box"].removed == [rec["id"]] and h["webapps"].calls == []


def test_a_failed_build_read_by_the_box_is_kept_on_the_record(world):
    # The box's reading of the failure is saved through the
    # real record store (the field is in the record's shape).
    from puffpai.box import driver as real_driver
    h = world(box=Box(build_ok=False))
    sys.modules["puffpai.box.driver"].build_failure = real_driver.build_failure
    ctx = sup.Ctx()
    out = apply.apply_code_project(ITEM, ctx)
    rec = records.get(out["integration_id"])
    assert out["status"] == "failed" and rec["status"] == "failed"
    assert rec["build_failure"] == {"step": "build", "cause": "build_failed"}
    assert out["build_failure"] == rec["build_failure"]
    assert real_driver.build_failure("build", {"ok": False})["line"] in ctx.lines
    assert h["box"].removed == [rec["id"]] and h["webapps"].calls == []


def test_the_lock_scan():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        assert apply.lock_offsite(d) is None
        with open(os.path.join(d, "package-lock.json"), "w", encoding="utf-8") as fh:
            fh.write(lock())
        assert apply.lock_offsite(d) is False
        for bad in ("file:../x", "http://registry.npmjs.org/a.tgz",
                    "https://registry.npmjs.org.evil.example/a.tgz"):
            with open(os.path.join(d, "package-lock.json"), "w", encoding="utf-8") as fh:
                fh.write(lock(bad))
            assert apply.lock_offsite(d) is True, bad


def test_a_build_folder_after_a_failed_try_is_exported_into_an_empty_folder(world):
    box = Box()
    box.out_dir = "build"        # "dist" is tried first and leaves a folder behind
    h = world(box=box)
    out = apply.apply_code_project(ITEM, sup.Ctx())
    assert out["status"] == "added", out
    web = h["webapps"].calls[0]["files_dir"]
    assert not os.path.exists(os.path.join(web, "partial"))


def test_a_front_end_in_its_own_folder_is_found(world, tmp_path):
    """A project whose web front end lives in a top folder of its own
    ("client/" with its own package.json) builds into client/dist: that is
    tried after the project's own dist, build and out, before public."""
    box = Box()
    box.out_dir = "client/dist"
    tree = str(tmp_path / "tree")
    sup.write(tree, "client/package.json", json.dumps({"name": "client"}))
    h = world(box=box)                       # the archive now holds client/
    assert apply.build_output_candidates(tree)[:6] == [
        "dist", "build", "out", "client/dist", "client/build", "client/out"]
    assert apply.build_output_candidates(tree)[-1] == "public"
    out = apply.apply_code_project(ITEM, sup.Ctx())
    assert out["status"] == "added", out
    assert h["webapps"].calls


def test_output_candidates_skip_links_dot_folders_and_odd_names(tmp_path):
    root = str(tmp_path / "t")
    for name in (".git", "node_modules", "we b", "web"):
        sup.write(root, f"{name}/index.html", "<html></html>")
    got = apply.build_output_candidates(root)
    assert "web/dist" in got
    assert not any(c.startswith((".git", "node_modules", "we b")) for c in got)


def test_a_workspace_link_inside_the_project_is_not_offsite(tmp_path):
    """npm writes a front end in its own folder as a workspace link
    ("node_modules/client": {"resolved": "client", "link": true}): not a
    download. A link out of the project still is."""
    def with_link(target):
        data = json.loads(lock())
        data["packages"]["client"] = {"name": "client", "version": "1.0.0"}
        data["packages"]["node_modules/client"] = {"resolved": target, "link": True}
        return json.dumps(data)
    for target, offsite in (("client", False), ("apps/web", False), ("../elsewhere", True),
                            ("/etc", True), ("file:../x", True), ("C:/x", True)):
        d = tmp_path / target.replace("/", "_").replace(":", "_").replace(".", "d")
        d.mkdir()
        (d / "package-lock.json").write_text(with_link(target), encoding="utf-8")
        assert apply.lock_offsite(str(d)) is offsite, target


@pytest.mark.parametrize("verdict,status", [("block", "refused"), ("hold", "held")])
def test_the_box_keeps_nothing_when_the_built_app_is_stopped_or_waits(world, monkeypatch,
                                                                       verdict, status):
    """The build ran, then the web app check
    stopped it; the project and its parts stayed in the locked box. Refused,
    or waiting for a yes (a yes builds it again), the box keeps nothing."""
    h = world(box=Box())
    monkeypatch.setitem(sys.modules, "puffpai.webapps.install", sup.fake_webapps_install(
        {"verdict": {"verdict": verdict, "reasons": ["orders"]}}))
    out = apply.apply_code_project(ITEM, sup.Ctx())
    rec = records.get(out["integration_id"])
    assert out["status"] == status and rec["status"] == status, out
    assert h["box"].removed == [rec["id"]]
    if status == "held":
        # Saying no afterwards leaves nothing either.
        from puffpai.security import consent_tickets
        consent_tickets.reset()
        apply.decide(rec["id"], False, device_id=sup.DEVICE if hasattr(sup, "DEVICE")
                     else "device:aaaa000000000001")
        assert records.get(rec["id"])["status"] == "refused"


def test_a_stopped_app_says_the_real_reason(world, monkeypatch):
    """Live finding: the web app check gives failed rows, not reasons; the
    owner heard "the checks could not finish" although they had."""
    world(box=Box())
    monkeypatch.setitem(sys.modules, "puffpai.webapps.install", sup.fake_webapps_install(
        {"verdict": {"verdict": "block", "rows": [
            {"code": "too_big", "result": "pass", "why": ""},
            {"code": "runtime_hosts_new", "result": "fail", "why": "www.w3.org"},
            {"code": "orders", "result": "fail", "why": "text that reads like orders"}]}}))
    ctx = sup.Ctx()
    out = apply.apply_code_project(ITEM, ctx)
    assert out["status"] == "refused"
    assert wording.BLOCK_REASONS["orders"] in out["line"], out["line"]
    assert wording.BLOCK_REASONS["check_failed"] not in out["line"]


# -- the lock npm ci really reads, and specs with no address ------------
#
# A clean package-lock.json next to an
# npm-shrinkwrap.json with a git address gave no hold, and the recorded lock
# was the wrong file; a lock v1 part with "version": "github:evil/x" and no
# "resolved" gave no hold either.

def _v1(dep):
    return json.dumps({"name": "party-app", "lockfileVersion": 1, "requires": True,
                       "dependencies": {"x": dep}})


def _write(d, name, text):
    with open(os.path.join(d, name), "w", encoding="utf-8") as fh:
        fh.write(text)


def test_the_shrinkwrap_is_read_too(tmp_path):
    d = str(tmp_path)
    _write(d, "package-lock.json", lock())
    _write(d, "npm-shrinkwrap.json", lock("git+https://evil.example/a.git"))
    assert apply.lock_offsite(d) is True
    assert apply.npm_lock_used(d) == "npm-shrinkwrap.json"
    _write(d, "npm-shrinkwrap.json", lock())
    assert apply.lock_offsite(d) is False
    _write(d, "npm-shrinkwrap.json", "{not json")
    assert apply.lock_offsite(d) is None                    # unreadable: no build
    os.remove(os.path.join(d, "package-lock.json"))
    _write(d, "npm-shrinkwrap.json", lock("https://evil.example/a.tgz"))
    assert apply.lock_offsite(d) is True


@pytest.mark.parametrize("dep,offsite", [
    ({"version": "github:evil/x"}, True),
    ({"version": "git+https://evil.example/x.git"}, True),
    ({"version": "evil/x#main"}, True),
    ({"version": "https://evil.example/x.tgz"}, True),
    ({"version": "file:../outside"}, True),
    ({"version": "x-1.0.0.tgz"}, True),
    ({"version": "1.2.3", "resolved": "https://registry.npmjs.org/x/-/x-1.2.3.tgz"}, False),
    ({"version": "1.2.3-rc.1", "requires": {"y": "^2.0.0 || ~3.1"}}, False),
    ({"version": "1.0.0", "requires": {"y": "~7.18.0", "z": "latest"}}, False),
    ({"version": "1.0.0", "requires": {"y": "~/home/x"}}, True),
    ({"version": "npm:string-width@4.2.3"}, False),
    ({"version": "file:client"}, False),
    ({"version": "1.2.3", "requires": {"y": "github:evil/y"}}, True),
    ({"version": "1.2.3", "dependencies": {"z": {"version": "gitlab:evil/z"}}}, True),
])
def test_a_part_with_no_address_says_where_it_comes_from(tmp_path, dep, offsite, monkeypatch):
    _write(str(tmp_path), "package-lock.json", _v1(dep))
    # This reader on its own, with the box's lock scan left out.
    monkeypatch.setattr(apply, "optional", lambda name: None)
    assert apply.lock_offsite(str(tmp_path)) is offsite, dep
    monkeypatch.undo()
    # With the box's stricter scan in, and either one is
    # enough, so a part with no download address or no fingerprint is held too.
    from puffpai.box import locks
    stricter = bool(locks.scan_npm_lock(_v1(dep).encode("utf-8")))
    assert apply.lock_offsite(str(tmp_path)) is (offsite or stricter), dep


def test_a_spec_in_the_project_itself_counts(tmp_path):
    data = json.loads(lock())
    data["packages"][""]["dependencies"] = {"x": "github:evil/x"}
    _write(str(tmp_path), "package-lock.json", json.dumps(data))
    assert apply.lock_offsite(str(tmp_path)) is True
    data["packages"][""]["dependencies"] = {"x": "^1.0.0", "client": "file:client"}
    _write(str(tmp_path), "package-lock.json", json.dumps(data))
    assert apply.lock_offsite(str(tmp_path)) is False


def test_the_recorded_lock_is_the_one_npm_ci_reads(world):
    sw = lock().replace("party-app", "party-app-shrinkwrap")
    world(box=Box(), shrinkwrap_text=sw)
    out = apply.apply_code_project(ITEM, sup.Ctx())
    assert out["status"] == "added", out
    rec = records.get(out["integration_id"])
    import hashlib
    assert rec["lock"]["path"] == "npm-shrinkwrap.json"
    assert rec["lock"]["sha256"] == hashlib.sha256(sw.encode()).hexdigest()


def test_an_offsite_shrinkwrap_waits_for_a_yes(world):
    h = world(box=Box(), shrinkwrap_text=lock("git+https://evil.example/a.git"))
    out = apply.apply_code_project(ITEM, sup.Ctx())
    rec = records.get(out["integration_id"])
    assert rec["status"] == "held" and rec["held"]["reasons"] == ["lock_offsite"]
    assert h["box"].runs == []


@pytest.mark.parametrize("web_lines,said", [([], True), (["KEEPS"], False)])
def test_one_reason_when_the_bot_cannot_use_the_app(world, monkeypatch, web_lines, said):
    """The web app step said "It keeps
    nothing of its own, ..." and Toolsmith then added a second, different
    reason. Toolsmith now says its own line only when that step said none."""
    import types
    keeps = "Your bot can open it for you. It keeps nothing of its own."
    web_wording = types.ModuleType("puffpai.webapps.wording")
    web_wording.KEEPS_NOTHING = keeps
    monkeypatch.setitem(sys.modules, "puffpai.webapps.wording", web_wording)
    world(box=Box())
    monkeypatch.setitem(sys.modules, "puffpai.webapps.install", sup.fake_webapps_install(
        {"drivable": False, "abilities": [], "lines": [keeps if l == "KEEPS" else l
                                                        for l in web_lines]}))
    ctx = sup.Ctx()
    out = apply.apply_code_project(ITEM, ctx)
    assert out["status"] == "added", out
    assert (wording.CANNOT_DRIVE in ctx.lines) is said, ctx.lines
