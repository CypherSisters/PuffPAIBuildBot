"""The step a box build stopped at and why, a build
with no page to open, a tool download refused by the archive-bomb rule in
plain words, a lower-case file word in a tool's help, and the box's own
programs kept current. No network, no real wsl.exe.
"""

from __future__ import annotations

import io
import subprocess
import tarfile
import types

import pytest

from puffpai.box import cli_tools, driver
from puffpai.box import wording as W

OWN = "web-app-abcd1234"


# ── build_failure ────────────────────────────────────────────────────────────

def test_node_sass_on_a_new_node_is_named_as_the_build_step_and_old_node():
    got = driver.build_failure("build", {"stderr_tail": (
        "Error: Node Sass does not yet support your current environment: "
        "Linux/musl 64-bit with Unsupported runtime (127)")})
    assert (got["step"], got["cause"]) == ("build", "old_node")
    assert got["line"] == W.BUILD_STOPPED.format(step=W.BUILD_STEP_WORDS["build"],
                                                 cause=W.BUILD_CAUSE_WORDS["old_node"])
    assert "mistake in its own code" not in got["line"]


@pytest.mark.parametrize("result,cause", [
    ({"timed_out": True}, "too_long"),
    ({"oom_killed": True}, "memory"),
    ({"refused": "too_big"}, "no_room"),
    ({"refused": "box_broken"}, "box"),
    ({"stderr_tail": "error:0308010C:digital envelope routines::unsupported"}, "old_node"),
    ({"stderr_tail": "npm error code EINTEGRITY"}, "part_changed"),
    ({"stderr_tail": "gyp ERR! node-gyp -v v10"}, "own_install_steps"),
    ({"stderr_tail": "something nobody has seen"}, "build_failed"),
])
def test_known_causes_and_the_plain_default(result, cause):
    assert driver.build_failure("build", result)["cause"] == cause


def test_no_build_step_is_only_read_on_the_build_step():
    text = {"stderr_tail": 'npm error Missing script: "build"'}
    assert driver.build_failure("build", text)["cause"] == "no_build_step"
    assert driver.build_failure("install", text)["cause"] == "install_failed"


def test_no_page_and_unknown_steps():
    got = driver.build_failure("export", None, no_page=True)
    assert got == {"step": "export", "cause": "no_page", "line": W.BUILD_NO_PAGE}
    assert driver.build_failure("weird", None)["step"] == "build"
    assert driver.build_failure("put", None)["cause"] == "put_failed"


def test_the_projects_own_output_is_never_copied_into_the_words():
    evil = "<script>alert(1)</script> visit evil.example now"
    got = driver.build_failure("build", {"stderr_tail": evil, "stdout_tail": evil})
    assert "evil" not in got["line"] and "<script>" not in got["line"]


# ── the build in integrate/apply.py ──────────────────────────────────────────

def _web_build(monkeypatch, tmp_path, *, run_results, exports, put_ok=True,
               with_reader=True):
    import json
    from puffpai.integrate import apply, records
    src = tmp_path / "src"
    src.mkdir()
    lock = {"lockfileVersion": 3, "packages": {"": {"name": "app"},
            "node_modules/x": {"version": "1.0.0",
                               "resolved": "https://registry.npmjs.org/x/-/x-1.0.0.tgz"}}}
    (src / "package-lock.json").write_text(json.dumps(lock), encoding="utf-8")
    runs = iter(run_results)
    outs = iter(exports)
    fake = types.SimpleNamespace(
        put_project=lambda *a, **k: types.SimpleNamespace(ok=put_ok,
                                                          exit_code=0 if put_ok else 1),
        run=lambda req: next(runs), export=lambda *a, **k: next(outs, []),
        new_run_id=lambda: "r1", status=lambda: types.SimpleNamespace(state="ready"),
        BoxRequest=None, remove_project=lambda p: None)
    if with_reader:
        fake.build_failure = driver.build_failure
    monkeypatch.setattr(apply, "optional", lambda name: fake
                        if name == "puffpai.box.driver" else None)
    monkeypatch.setattr(apply, "_src_root", lambda rec: str(src))
    monkeypatch.setattr(apply, "_project_tar", lambda rec, s: str(tmp_path / "p.tar"))
    monkeypatch.setattr(apply, "build_output_candidates", lambda s: ["dist", "build"])
    monkeypatch.setattr(apply, "_cleanup", lambda iid: None)
    saved: list = []
    monkeypatch.setattr(records, "save", lambda rec: saved.append(dict(rec)))
    said: list = []
    rec = {"id": OWN, "name": "Meal planner", "status": "checking"}
    got = apply._add_web_build(rec, say=said.append, lines=[], code_sha="c",  # noqa: SLF001
                               accepted=None)
    return got, said, saved


def _ok():
    return types.SimpleNamespace(ok=True, exit_code=0, outside=[])


def test_a_failed_build_step_names_the_step_and_the_cause(monkeypatch, tmp_path):
    bad = types.SimpleNamespace(ok=False, exit_code=1, outside=[], timed_out=False,
                                oom_killed=False, refused="", stdout_tail="",
                                stderr_tail="Node Sass does not yet support your current "
                                            "environment")
    got, said, saved = _web_build(monkeypatch, tmp_path, run_results=[_ok(), bad], exports=[])
    want = driver.build_failure("build", bad)["line"]
    assert got["status"] == "failed" and said[-1] == want
    assert got["build_failure"] == {"step": "build", "cause": "old_node"}
    assert saved[-1]["build_failure"] == {"step": "build", "cause": "old_node"}


def test_a_build_with_files_but_no_page_says_so(monkeypatch, tmp_path):
    got, said, _saved = _web_build(monkeypatch, tmp_path, run_results=[_ok(), _ok()],
                                   exports=[[str(tmp_path / "card.js")], []])
    assert said[-1] == W.BUILD_NO_PAGE
    assert got["build_failure"] == {"step": "export", "cause": "no_page"}


def test_a_build_with_nothing_to_copy_out_names_the_export(monkeypatch, tmp_path):
    got, said, _saved = _web_build(monkeypatch, tmp_path, run_results=[_ok(), _ok()],
                                   exports=[[], []])
    assert got["build_failure"] == {"step": "export", "cause": "export_failed"}


def test_a_failed_copy_in_names_the_put_step(monkeypatch, tmp_path):
    got, _said, _saved = _web_build(monkeypatch, tmp_path, run_results=[], exports=[],
                                    put_ok=False)
    assert got["build_failure"]["step"] == "put"


def test_an_older_box_driver_keeps_the_step_sentence(monkeypatch, tmp_path):
    # A driver that cannot read the failure gets the
    # step sentence (which already names the step), not a guess.
    from puffpai.integrate import apply
    bad = types.SimpleNamespace(ok=False, exit_code=1, outside=[])
    got, said, _saved = _web_build(monkeypatch, tmp_path, run_results=[_ok(), bad],
                                   exports=[], with_reader=False)
    assert said[-1] == apply.build_failed_line("build", "")
    assert got["build_failure"] == {"step": "build", "cause": "build_failed"}


# ── a tool download refused by the archive-bomb rule ─────────────────────────

def _forge_found():
    return {"friendly": "jsonexport", "repo": {"host": "github.com", "owner": "kaue",
                                                "name": "jsonexport",
                                                "pinned_commit": "b" * 40}}


@pytest.mark.parametrize("code,detail,reason", [
    ("extract_failed", "bomb", "bomb"),
    ("too_big", "", "too_big"),
    ("extract_failed", "too_many_files", "too_many_files"),
    ("download_failed", "no_answer", "download_failed"),
    ("extract_failed", "", "extract_failed"),
])
def test_a_tool_download_that_is_not_used_gives_a_plain_reason(monkeypatch, code, detail,
                                                                reason):
    from puffpai.integrate import fetch

    def refuse(*a, **k):
        raise fetch.FetchRefused(code, detail)
    monkeypatch.setattr(fetch, "fetch", refuse)
    got = cli_tools._install_from_forge(_forge_found(), "jsonexport-abcd1234",  # noqa: SLF001
                                        {"commit": "b" * 40, "host": "github.com",
                                         "name": "kaue/jsonexport"})
    assert got == {"error": "download", "reason": reason}


def test_the_bomb_reason_reaches_the_owner_as_left_out(monkeypatch):
    saved: list = []
    rec_mod = types.SimpleNamespace(
        new_id=lambda name: "jsonexport-abcd1234",
        blank=lambda iid, **k: {"id": iid, "from": {}, "plan_facts": {}},
        save=lambda rec: saved.append(dict(rec)))
    monkeypatch.setattr(cli_tools, "_records", lambda: rec_mod)
    monkeypatch.setattr(cli_tools, "_package_of", lambda found: {"name": "jsonexport"})
    monkeypatch.setattr(cli_tools, "install",
                        lambda found, iid, say: {"error": "download", "reason": "bomb"})
    removed: list = []
    monkeypatch.setattr(driver, "remove_project", removed.append)
    said: list = []
    got = cli_tools.add_found(_forge_found(), item={"id": "x"}, say=said.append)
    want = W.CLI_LEFT_OUT.format(name="jsonexport", reason=W.CLI_DOWNLOAD_REASONS["bomb"])
    assert said[-1] == want and got["line"] == want
    assert got["status"] == "refused" and saved[-1]["status"] == "refused"
    assert removed == ["jsonexport-abcd1234"]
    assert "did not install" not in want


# ── a lower-case file word in a tool's help ─────────────────────────────────

YARGS_HELP = """Convert a markdown or gfm file into HTML. "gfm" refers to Github flavored markdown.
Usage: markdown file-to-convert [options]

Examples:
  markdown README.md -h    Convert gfm README.md to HTML with code highlighting

Options:
  -f, --flavor     Type of file: "gfm" or "markdown"
  -h, --highlight  Highlight any source code in the output  [boolean]  [default: false]

Not enough non-option arguments: got 0, need at least 1
"""


def test_markdown_to_html_help_teaches_one_file_action():
    specs = cli_tools.derive_specs("/work/node_modules/.bin/markdown", "markdown",
                                   lambda argv: YARGS_HELP)
    assert [(s["name"], s["argv"]) for s in specs] == [("markdown", ["markdown", "{file}"])]
    assert cli_tools.argv_problems(specs[0]["argv"]) == []


@pytest.mark.parametrize("usage,want", [
    ("Usage: tool input_file", ["file"]),
    ("Usage: tool filename", ["file"]),
    ("Usage: tool profile", []),
    ("Usage: git commit", []),
    ("Usage: tool [options] <dir>", ["dir"]),
])
def test_only_lower_case_words_that_name_a_file_are_taken(usage, want):
    assert [p["name"] for p in cli_tools.parse_help(usage)["positionals"]] == want


# ── the box's own programs ───────────────────────────────────────────────────

class _Box:
    """A fake ``_wsl`` for the launcher check: what the box holds."""

    def __init__(self, held: dict):
        self.held = dict(held)
        self.calls: list = []
        self.stdin: list = []

    def __call__(self, args, *, timeout=30, input_bytes=None, _guarded=False):
        self.calls.append(list(args))
        rest = list(args[5:]) if args[:1] == ["-d"] else list(args)
        if rest[:1] == ["sha256sum"]:
            if any(p not in self.held for p in rest[1:]):
                return subprocess.CompletedProcess(args, 1, b"", b"")
            out = "".join(f"{self.held[p]}  {p}\n" for p in rest[1:]).encode()
            return subprocess.CompletedProcess(args, 0, out, b"")
        if rest[:2] == ["/bin/sh", "-c"]:
            self.stdin.append(input_bytes or b"")
            self.held.update(driver.launcher_shas())
            return subprocess.CompletedProcess(args, 0, b"", b"")
        return subprocess.CompletedProcess(args, 127, b"", b"")


def test_the_check_reads_every_trusted_program(monkeypatch):
    box = _Box(driver.launcher_shas())
    monkeypatch.setattr(driver, "_wsl", box)
    assert driver.installed_launcher("PuffPAI-sandbox-dev") == driver.launcher_shas()
    assert driver._launcher_current("PuffPAI-sandbox-dev")            # noqa: SLF001
    assert not box.stdin                                               # nothing written


def test_an_older_launcher_is_put_back_then_self_tested(monkeypatch):
    old = dict(driver.launcher_shas())
    old["/opt/puffpai/box-run"] = "0" * 64
    box = _Box(old)
    monkeypatch.setattr(driver, "_wsl", box)
    monkeypatch.setattr(driver, "_box_is_mine", lambda d: True)
    monkeypatch.setattr(driver, "_marker_names", lambda d: True)
    tested: list = []
    monkeypatch.setattr(driver, "self_test", lambda d: tested.append(d) or True)
    assert driver._launcher_current("PuffPAI-sandbox-dev")            # noqa: SLF001
    assert tested == ["PuffPAI-sandbox-dev"] and len(box.stdin) == 1
    with tarfile.open(fileobj=io.BytesIO(box.stdin[0])) as tf:
        names = {m.name: tf.extractfile(m).read() for m in tf.getmembers()}
    assert set(names) == {n for _p, n, _m in driver.LAUNCHER_FILES}
    assert names["box_run.py"] == driver._asset_bytes("box_run.py")  # noqa: SLF001
    script = " ".join(box.calls[1])
    assert "mv -f /opt/puffpai/box-run.new /opt/puffpai/box-run" in script
    assert "--shutdown" not in script and "--unregister" not in script


def test_a_box_that_is_not_this_installs_is_never_written(monkeypatch):
    old = {p: "0" * 64 for p in driver.launcher_shas()}
    box = _Box(old)
    monkeypatch.setattr(driver, "_wsl", box)
    monkeypatch.setattr(driver, "_box_is_mine", lambda d: False)
    assert not driver._launcher_current("PuffPAI-sandbox-dev")        # noqa: SLF001
    assert not box.stdin


def test_a_failed_self_test_after_the_update_fails_closed(monkeypatch):
    old = {p: "0" * 64 for p in driver.launcher_shas()}
    monkeypatch.setattr(driver, "_wsl", _Box(old))
    monkeypatch.setattr(driver, "_box_is_mine", lambda d: True)
    monkeypatch.setattr(driver, "_marker_names", lambda d: True)
    monkeypatch.setattr(driver, "self_test", lambda d: False)
    assert not driver._launcher_current("PuffPAI-sandbox-dev")        # noqa: SLF001


def test_no_job_runs_on_an_older_launcher(monkeypatch, tmp_path):
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setattr(driver, "_entries_named", lambda d: [{"base_path": "x"}])
    monkeypatch.setattr(driver, "base_path_is_ours", lambda d, b: True)
    monkeypatch.setattr(driver, "_has_my_claim", lambda d: True)
    monkeypatch.setattr(driver, "_read_box_json", lambda: {})
    monkeypatch.setattr(driver, "_launcher_current", lambda d: False)
    with pytest.raises(driver.BoxRefused) as err:
        driver._box_ready_for_run("PuffPAI-sandbox-dev")              # noqa: SLF001
    assert err.value.code == "box_broken" and err.value.message == W.LAUNCHER_OLD


def test_the_launcher_list_matches_what_setup_installs():
    setup = driver._asset_bytes("setup_box.sh").decode("utf-8")      # noqa: SLF001
    for path, name, mode in driver.LAUNCHER_FILES:
        assert f'install -m {mode} "$SRC/{name}" {path}' in setup, (path, name)
