"""Install what's missing: the requirements reader, the one-yes plan, the
Drawbridge package check, keys named only in a skill's text, sibling skills
from the same publisher, and a Find more update that keeps a skill's key.

The skill texts below are short excerpts of the owner's installed skills
(MusicBot). Nothing is installed and nothing goes online:
the installer and the registry lookups are replaced in every test.
"""
from __future__ import annotations

import json
import os

import pytest

from keys_support import fast_vault, reset_keys
from puffpai import api_server, brain_commands, deps, drawbridge, skills, things
from puffpai.finding import install
from puffpai.security import keys

AUTH = {"Authorization": f"Bearer {api_server._API_KEY}"}
PAIRED = {**AUTH, "X-Puff-Device-Id": "deviceaaaa000001"}
TOKEN = "bc_live_9f8e7d6c5b4a39281706f5e4"

MUSIC = """---
name: music
description: Generate music from a text description.
---
Confirm the live model and its schema via the `runware-models` + `runware-run` skills before calling.

1. Resolve the model schema (`runware-run`) and confirm the field names.

## Related skills

`runware-run`, `runware-models`, `runware-prompting`; `add-audio-to-video` (score a video).
"""
ELEVEN = """---
name: elevenlabs-music-generation
allowed-tools: Bash(runcomfy *)
description: Generate songs with ElevenLabs Music on RunComfy via the `runcomfy` CLI.
homepage: https://www.runcomfy.com
---
[runcomfy.com](https://www.runcomfy.com/?utm_source=skills.sh)

## Install this skill

```bash
npx skills add agentspace-so/runcomfy-agent-skills --skill elevenlabs-music-generation -g
```

```bash
# 1. Install (one of, see runcomfy-cli skill for details)
npm i -g @runcomfy/cli                              # global install
npx -y @runcomfy/cli --version                      # zero-install
runcomfy login                                      # or in CI: export RUNCOMFY_TOKEN=<token>
```
- the `npm` / `npx` lines are one-time operator setup.
CLI deep dive: [`runcomfy-cli`](https://www.skills.sh/agentspace-so/runcomfy-agent-skills/runcomfy-cli) skill.
"""
VIDEO = """---
name: music-to-video
description: Turn a music track into a beat-synced video.
---
> First, keep this skill fresh: `npx hyperframes skills update music-to-video`.

Write `BRIEF.md` after init (never before, `init` refuses a non-empty directory).
Name it in kebab-case, such as `midnight-drive-loop`; `init` checks the installed skills.

Prerequisites: Python 3 with `librosa`, `numpy`, and `soundfile` available:

```bash
python3 -m pip install librosa numpy soundfile
```
"""
MEMORY = """---
name: music-memory
description: A practice journal in BlueColumn. Requires a BlueColumn API key (bc_live_*).
---
```bash
curl -X POST https://xkjkwqbfvkswwdmbtndo.supabase.co/functions/v1/agent-remember \\
  -H "Authorization: Bearer $BLUECOLUMN_API_KEY"
```
Get a key at https://bluecolumn.ai/keys .
"""
SONGSEE = """---
name: songsee
description: Spectrograms with the songsee CLI.
homepage: https://github.com/steipete/songsee
metadata: {"clawdbot":{"requires":{"bins":["songsee"]},"install":[{"id":"brew","kind":"brew","formula":"steipete/tap/songsee","bins":["songsee"],"label":"Install songsee (brew)"}]}}
---
songsee track.mp3 -o out.png
"""


# ── reading what a skill needs ─────────────────────────────────────────────

def _names(rows, kind=None):
    return [r["name"] for r in rows if kind is None or r.get("kind") == kind]


RUNWARE = {"source": "clawhub", "owner": "runware"}
RUNCOMFY = {"source": "github", "repo": "agentspace-so/runcomfy-agent-skills"}


def test_sibling_skills_are_read_from_the_text():
    got = deps.read(MUSIC, origin=RUNWARE)
    assert _names(got["skills"]) == ["runware-models", "runware-run", "runware-prompting",
                                      "add-audio-to-video"]
    assert all(s["from"] == "text" for s in got["skills"])
    assert got["programs"] == [] and got["packages"] == [] and got["keys"] == []


def test_no_sibling_is_offered_without_a_known_publisher():
    # The installer installs a sibling only from the same publisher, so a
    # skill with none known (a short ClawHub id locked with no owner) offers
    # none.
    assert deps.read(MUSIC)["skills"] == []
    assert deps.read(MUSIC, origin={"source": "clawhub", "owner": ""})["skills"] == []


def test_a_cli_its_npm_package_and_its_key_are_read():
    got = deps.read(ELEVEN, origin=RUNCOMFY)
    assert "runcomfy" in _names(got["programs"])
    assert _names(got["packages"], "npm") == ["@runcomfy/cli"]          # never "skills"
    key = got["keys"][0]
    assert key["name"] == "RUNCOMFY_TOKEN" and key["from"] == "text"
    assert key["get_url"].startswith("https://www.runcomfy.com/")
    assert _names(got["skills"]) == ["runcomfy-cli"]                    # not npm, npx


def test_python_packages_and_no_false_sibling_skills():
    got = deps.read(VIDEO)
    assert _names(got["packages"], "pip") == ["librosa", "numpy", "soundfile"]
    assert got["skills"] == []                       # init, midnight-drive-loop are not skills
    assert "npx" in _names(got["programs"])


def test_a_key_named_only_in_the_text_gets_its_own_sites_link():
    got = deps.read(MEMORY)
    assert got["keys"] == [{"name": "BLUECOLUMN_API_KEY", "from": "text", "prompt": "",
                            "help": "", "get_url": "https://bluecolumn.ai/keys"}]


def test_a_brew_only_program_is_one_she_installs_herself():
    got = deps.read(SONGSEE)
    assert _names(got["programs"]) == ["songsee"]
    assert got["manual"][0]["name"] == "songsee" and got["manual"][0]["how"] == "brew"


def test_names_that_are_not_plain_are_never_kept():
    text = ("---\nname: x\n---\n"
            "pip install 'evil;rm' ../local.whl git+https://x.invalid/a good-one==1.2.3\n"
            "npm i -g ../x @ok/pkg@2.0.0 $(id)\n"
            "echo $PUFFPAI_API_KEY $NODE_OPTIONS $LD_PRELOAD_KEY ${GOOD_API_KEY} $YOUR_API_KEY\n")
    got = deps.read(text)
    assert [(p["kind"], p["name"], p["version"]) for p in got["packages"]] == [
        ("pip", "good-one", "1.2.3"), ("npm", "@ok/pkg", "2.0.0")]
    assert _names(got["keys"]) == ["GOOD_API_KEY"]
    for bad in ("PATH", "NODE_OPTIONS", "LD_PRELOAD", "PUFFPAI_API_KEY", "GIT_TOKEN", "A=B"):
        assert not deps.env_name_ok(bad)
    assert deps.env_name_ok("RUNWARE_API_KEY")


def test_package_files_in_the_folder_are_read(tmp_path):
    (tmp_path / "SKILL.md").write_text("---\nname: p\n---\n", encoding="utf-8")
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "requirements.txt").write_text(
        "requests==2.32.3\n# note\n-e .\nmido>=1.3\n", encoding="utf-8")
    (tmp_path / "package.json").write_text(json.dumps(
        {"dependencies": {"tone": "^15.0.4", "../evil": "1.0.0"}}), encoding="utf-8")
    got = deps.read(folder=str(tmp_path))
    assert [(p["name"], p["version"], p["from"]) for p in got["packages"]] == [
        ("tone", "15.0.4", "declared"), ("requests", "2.32.3", "declared"),
        ("mido", "", "declared")]
    assert "node" in _names(got["programs"])


# ── at run time ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("output,code,command,want", [
    ("bash: runcomfy: command not found", 127, "runcomfy run x", {"programs": ["runcomfy"]}),
    ("/usr/bin/bash: line 1: songsee: command not found", 127, "", {"programs": ["songsee"]}),
    ("'ffmpeg' is not recognized as an internal or external command,", 1, "",
     {"programs": ["ffmpeg"]}),
    ("Traceback...\nModuleNotFoundError: No module named 'librosa.core'", 1, "",
     {"modules": ["librosa"]}),
    ("Error: Cannot find module '@runcomfy/cli/lib/x'", 1, "", {"node_modules": ["@runcomfy/cli"]}),
    ("", 127, "hyperframes render", {"programs": ["hyperframes"]}),
    ("Error: Cannot find module './local'", 1, "", {}),
])
def test_a_failed_command_says_what_is_missing(output, code, command, want):
    got = deps.missing_from_output(output, code, command)
    for key in ("programs", "modules", "node_modules"):
        assert got[key] == want.get(key, []), (key, got)


# ── the plan, the check and the install ────────────────────────────────────

@pytest.fixture
def world(monkeypatch, tmp_path):
    state = tmp_path / "state"
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(state))
    monkeypatch.setattr(brain_commands, "_vault_dir", lambda: str(tmp_path / "vault"))
    reset_keys()
    v = fast_vault(monkeypatch, tmp_path / "vault")
    keys.set_vault_provider(lambda: v)
    for name, md in (("music-to-video", VIDEO), ("music-memory", MEMORY),
                     ("elevenlabs-music-generation", ELEVEN)):
        folder = state / "skills" / name
        folder.mkdir(parents=True)
        (folder / "SKILL.md").write_text(md, encoding="utf-8")
    (state / "skills" / ".lock.json").write_text(json.dumps(
        {"elevenlabs-music-generation": {**RUNCOMFY, "folder": "skills/elevenlabs-music-generation"}}),
        encoding="utf-8")
    # Only node on this computer, nothing in the tools folders, and no
    # system installer found (a test never reaches winget or apt-get).
    monkeypatch.setattr(deps, "has_program", lambda name: name in ("node", "npx", "npm"))
    from puffpai.security import host_programs
    real_path = host_programs.path
    monkeypatch.setattr(host_programs, "path", lambda name: None if name in (
        "winget", "apt-get") else real_path(name))
    monkeypatch.setattr(deps, "has_package", lambda kind, name: False)
    # Drawbridge's package checklist, as factory_wiring registers it.
    monkeypatch.setitem(drawbridge._CHECKERS, deps.KIND, {  # noqa: SLF001
        "rows_fn": deps.package_rows, "block": deps.BLOCK_CODES, "hold": frozenset(),
        "version": deps.CHECKER_VERSION})
    monkeypatch.setattr(deps, "_switch_off", lambda: False)
    facts = {"pip": {"exists": True, "wheel": True, "yanked": False},
             "npm": {"exists": True, "tarball_ok": True, "scripts": ["postinstall"]}}
    monkeypatch.setattr(deps, "_registry_facts",
                        lambda step: ("1.2.3", dict(facts[step["kind"]])))
    ran: list = []

    def fake_installer(argv, extra_env=None):
        from puffpai.security import host_programs
        ran.append({"argv": list(argv), "env": {**host_programs.run_kwargs()["env"],
                                                 **(extra_env or {})}})
        return {"code": 0, "output": "ok"}
    monkeypatch.setattr(deps, "_run_installer", fake_installer)
    deps._PLANS.clear()  # noqa: SLF001
    api_server.app.config.update(TESTING=True)
    yield {"vault": v, "state": state, "ran": ran, "facts": facts,
           "client": api_server.app.test_client()}
    keys.set_vault_provider(None)
    deps._PLANS.clear()  # noqa: SLF001


def test_the_plan_lists_exact_steps_in_plain_words(world):
    plan = deps.make_plan("music-to-video", thing="skill-music-to-video")
    shown = deps.public_plan(plan)
    assert shown["plan"] and shown["thing"] == "skill-music-to-video"
    assert [s["name"] for s in plan["steps"] if s["kind"] == "pip"] == [
        "librosa", "numpy", "soundfile"]
    assert all("read from its text" in w for w in shown["steps"] if "Python" in w)
    for text in shown["steps"] + shown["manual"] + [deps.plan_line(plan)]:
        assert "\u2014" not in text and "door" not in text.lower() and "beta" not in text.lower()


def test_a_module_from_an_error_is_mapped_only_when_known(world):
    plan = deps.make_plan("", found={"programs": [], "modules": ["cv2", "mysterymod"],
                                     "node_modules": []})
    assert [s["name"] for s in plan["steps"]] == ["opencv-python"]
    assert any("mysterymod" in m for m in plan["manual"])


def test_one_yes_installs_exactly_the_plan_pinned_with_no_keys_in_env(world, monkeypatch):
    monkeypatch.setenv("SOME_API_KEY", "should-never-reach-an-installer")
    monkeypatch.setenv("PUFFPAI_API_KEY", "0123456789abcdef" * 4)
    plan = deps.make_plan("music-to-video")
    assert deps.run_plan(plan["id"], said_yes=False)["lines"][0].startswith("Nothing")
    assert world["ran"] == []
    out = deps.run_plan(plan["id"], said_yes=True, device="device:deviceaaaa000001")
    assert out["ok"] and out["done"] == ["librosa", "numpy", "soundfile"], out
    argv = world["ran"][0]["argv"]
    assert argv[1:4] == ["-m", "pip", "install"]
    assert "--only-binary=:all:" in argv and "--isolated" in argv
    assert argv[argv.index("--target") + 1] == deps.py_target()
    assert argv[-1] == "librosa==1.2.3"
    for r in world["ran"]:
        assert not any("KEY" in k.upper() or "TOKEN" in k.upper() for k in r["env"]), r["env"]
        assert "should-never-reach-an-installer" not in json.dumps(r["env"])
    # One yes, one run: the same plan cannot be replayed.
    assert deps.run_plan(plan["id"], said_yes=True)["code"] == "stale"
    # The security log has one line per install, names only.
    from puffpai.security.audit_log import _default_log_file
    log = open(_default_log_file(), encoding="utf-8").read()
    assert log.count('"install_missing"') == 3 and "librosa" in log


def test_npm_installs_skip_scripts_and_come_from_the_registry(world, monkeypatch):
    from puffpai.security import host_programs
    monkeypatch.setattr(host_programs, "path", lambda name: "/fixed/node" if name == "node"
                        else None)
    monkeypatch.setattr(host_programs, "npm_cli", lambda node=None: "/fixed/npm-cli.js")
    argv = deps.installer_argv({"kind": "npm", "name": "@runcomfy/cli"}, "1.2.3")
    assert argv[:3] == ["/fixed/node", "/fixed/npm-cli.js", "install"]
    assert "--ignore-scripts" in argv and "--registry=https://registry.npmjs.org/" in argv
    assert argv[-1] == "@runcomfy/cli@1.2.3"
    assert deps.installer_argv({"kind": "npm", "name": "../evil"}, "1.0.0") is None
    assert deps.installer_argv({"kind": "pip", "name": "ok"}, "") is None      # unpinned
    # winget and apt-get take only names from BuildBot's own list.
    monkeypatch.setattr(host_programs, "path", lambda name: "C:/x/winget.exe")
    assert deps.installer_argv({"kind": "winget", "name": "Evil.Package"}, "") is None
    got = deps.installer_argv({"kind": "winget", "name": "Gyan.FFmpeg"}, "")
    assert got[:5] == ["C:/x/winget.exe", "install", "--id", "Gyan.FFmpeg", "--exact"]


def test_drawbridge_refuses_a_package_with_no_ready_built_copy(world):
    world["facts"]["pip"]["wheel"] = False
    plan = deps.make_plan("music-to-video")
    out = deps.run_plan(plan["id"], said_yes=True)
    assert not out["ok"] and out["done"] == [] and world["ran"] == []
    assert any("not installed" in line for line in out["lines"])


def test_a_package_not_on_the_registry_is_refused(world, monkeypatch):
    monkeypatch.setattr(deps, "_registry_facts", lambda step: ("", {"exists": False}))
    verdict, _v = deps.check_step({"kind": "pip", "name": "nosuchpkg-xyz"})
    assert verdict["verdict"] == "block"
    assert {"not_on_registry", "not_pinned"} <= set(verdict["reasons"])


def test_registries_switched_off_install_nothing(world, monkeypatch):
    monkeypatch.setattr(deps, "_switch_off", lambda: True)
    plan = deps.make_plan("music-to-video")
    out = deps.run_plan(plan["id"], said_yes=True)
    assert out["failed"] and world["ran"] == []


# ── the page and the route ─────────────────────────────────────────────────

def test_the_page_shows_the_plan_and_the_get_a_key_link(world):
    needs = things.skill_needs("music-memory")
    assert needs["keys"][0]["name"] == "BLUECOLUMN_API_KEY"
    assert needs["keys"][0]["get_url"] == "https://bluecolumn.ai/keys"
    assert "BLUECOLUMN_API_KEY" in needs["line"]
    video = things.skill_needs("music-to-video")
    assert video["install"]["plan"] and len(video["install"]["steps"]) >= 3


def test_the_install_route_needs_a_paired_device_and_a_yes(world):
    plan = things.skill_needs("music-to-video")["install"]["plan"]
    c = world["client"]
    assert c.post("/things/install-missing", headers=AUTH, json={"plan_id": plan,
                                                                 "yes": True}).status_code == 403
    no = c.post("/things/install-missing", headers=PAIRED, json={"plan_id": plan})
    assert no.status_code == 409 and world["ran"] == []
    plan = things.skill_needs("music-to-video")["install"]["plan"]
    yes = c.post("/things/install-missing", headers=PAIRED, json={"plan_id": plan, "yes": True})
    body = yes.get_json()
    assert yes.status_code == 200 and body["ok"] and "needs" in body
    stale = c.post("/things/install-missing", headers=PAIRED, json={"plan_id": "x" * 16,
                                                                   "yes": True})
    assert stale.status_code == 409


# ── keys named only in the text ────────────────────────────────────────────

def test_a_text_named_key_can_be_kept_and_reaches_only_that_skill(world):
    got = world["client"].post("/things/skill-music-memory/key", headers=PAIRED,
                               json={"name": "BLUECOLUMN_API_KEY", "value": TOKEN})
    assert got.status_code == 200, got.get_json()
    assert TOKEN not in json.dumps(got.get_json())
    assert keys.skill_keys_set("music-memory") == ["BLUECOLUMN_API_KEY"]
    bad = world["client"].post("/things/skill-music-memory/key", headers=PAIRED,
                               json={"name": "OTHER_API_KEY", "value": TOKEN})
    assert bad.status_code == 400
    # The key is kept for this skill, but it
    # no longer hands it to a plain command, even the skill's own script: a key
    # reaches a command only through a checked web request. So the script here
    # sees no key in either folder.
    script = "import os\nprint(len(os.environ.get('BLUECOLUMN_API_KEY', '')))\n"
    for name in ("music-memory", "music-to-video"):
        (world["state"] / "skills" / name / "count.py").write_text(script, encoding="utf-8")
    for name in ("music-memory", "music-to-video"):
        got = brain_commands.run({"command": "python3 count.py", "skill": name},
                                 {"said_yes": True})
        assert got["output"].strip() == "0" and TOKEN not in got["output"]


def test_a_forbidden_name_is_never_kept_even_when_declared(world):
    with pytest.raises(keys.KeyRefused):
        keys.put_skill_key("music-memory", "NODE_OPTIONS", TOKEN, declared=["NODE_OPTIONS"])


# ── chat: a failed command shows the card ──────────────────────────────────

def test_a_command_not_found_comes_back_with_the_card(world):
    cmd = ("python3 -c \"import sys;print('bash: runcomfy: command not found',"
           "file=sys.stderr);sys.exit(127)\"")
    got = brain_commands.run({"command": cmd, "skill": "elevenlabs-music-generation"},
                             {"said_yes": True})
    card = got["missing"]
    assert card["plan"] and card["thing"] == "skill-elevenlabs-music-generation"
    assert any("@runcomfy/cli" in s for s in card["steps"])
    assert any("runcomfy-cli" in s for s in card["steps"])               # its sibling skill
    assert card["keys"][0]["name"] == "RUNCOMFY_TOKEN"
    assert "Install what's missing" in got["note"] and world["ran"] == []


def test_a_plain_failure_with_nothing_missing_has_no_card(world, monkeypatch):
    monkeypatch.setattr(deps, "has_program", lambda name: True)
    monkeypatch.setattr(deps, "has_package", lambda kind, name: True)
    got = brain_commands.run({"command": "python3 -c \"raise SystemExit(3)\""},
                             {"said_yes": True})
    assert "missing" not in got and got["exit_code"] == 3


# ── sibling skills and updates ─────────────────────────────────────────────

def _fake_clawhub(monkeypatch, texts: dict, version="1.0.0"):
    from puffpai.finding import sites as S
    monkeypatch.setattr(S, "clawhub_verify", lambda slug, owner="": (
        {"verdict": "clean", "version": version, "files": [], "summary": ""}
        if slug in texts else None))
    monkeypatch.setattr(install, "clawhub_skill", lambda slug, owner, v: {
        "ok": True, "files": {"SKILL.md": texts[slug].encode()}, "zip_sha256": "0" * 64})
    monkeypatch.setattr(install, "_switch_off_line", lambda f, s: None)
    monkeypatch.setattr(install, "_with_page", lambda out: out)


def _find(slug):
    return {"id": f"clawhub:runware/{slug}", "name": slug, "type": "skill", "can": "install",
            "site_name": "ClawHub", "url": "", "raw": {}}


def test_sibling_skills_from_the_same_publisher_install_with_it(world, monkeypatch):
    sib = "---\nname: runware-run\ndescription: Run a Runware model.\n---\nUse $RUNWARE_API_KEY.\n"
    _fake_clawhub(monkeypatch, {"music": MUSIC, "runware-run": sib, "runware-models": sib.replace(
        "runware-run", "runware-models")})
    lines: list = []
    out = install._install_skill(_find("music"), lines.append, [])  # noqa: SLF001
    assert out["ok"], lines
    have = {r["folder"] for r in skills.installed()}
    assert {"music", "runware-run", "runware-models"} <= have
    assert "runware-prompting" not in have and "add-audio-to-video" not in have
    lock = install._read_lock()  # noqa: SLF001
    assert lock["runware-run"]["owner"] == "runware"
    assert any("no skill by that name" in line for line in lines)
    # The sibling's own key can be kept on its page.
    assert deps.key_names("runware-run") == ["RUNWARE_API_KEY"]


def test_a_find_more_update_of_the_same_skill_keeps_its_key(world, monkeypatch):
    sib = "---\nname: runware-run\ndescription: Run.\n---\nUse $RUNWARE_API_KEY.\n"
    _fake_clawhub(monkeypatch, {"runware-run": sib}, version="1.0.0")
    assert install._install_skill(_find("runware-run"), lambda _l: None, [])["ok"]  # noqa: SLF001
    keys.put_skill_key("runware-run", "RUNWARE_API_KEY", TOKEN, declared=["RUNWARE_API_KEY"])
    _fake_clawhub(monkeypatch, {"runware-run": sib + "\nNew in 1.1.\n"}, version="1.1.0")
    out = install._install_skill(_find("runware-run"), lambda _l: None, [])  # noqa: SLF001
    assert out["ok"] and out["folder"] == "runware-run"           # updated, not runware-run-2
    assert [r["folder"] for r in skills.installed()].count("runware-run") == 1
    assert install._read_lock()["runware-run"]["version"] == "1.1.0"  # noqa: SLF001
    assert "New in 1.1." in skills.view("runware-run")["text"]
    assert keys.skill_keys("runware-run", ["RUNWARE_API_KEY"]) == {"RUNWARE_API_KEY": TOKEN}


def test_an_update_from_another_publisher_is_refused(world, monkeypatch):
    sib = "---\nname: runware-run\n---\n"
    _fake_clawhub(monkeypatch, {"runware-run": sib})
    assert install._install_skill(_find("runware-run"), lambda _l: None, [])["ok"]  # noqa: SLF001
    with pytest.raises(ValueError):
        install.update_skill("runware-run", {"SKILL.md": b"---\nname: x\n---\n"},
                             {"source": "clawhub", "owner": "someone-else", "slug": "runware-run"})


def test_a_key_kept_before_this_change_still_counts(world):
    folder = world["state"] / "skills" / "music-memory"
    assert folder.is_dir()
    new, legacy = keys._identities("music-memory")  # noqa: SLF001
    assert new and legacy and new != legacy
    assert keys._same_copy(legacy, "music-memory")  # noqa: SLF001
    assert not keys._same_copy("0" * 32, "music-memory")  # noqa: SLF001


def test_the_round3_wiring_registers_the_package_check():
    from puffpai import factory_wiring
    assert "puffpai.deps" in factory_wiring.ROUND3_WIRING
    rows = deps.package_rows({"manager": "pip", "package": "ok", "version": "1.0",
                              "facts": {"exists": True, "wheel": True}})
    assert all(r["result"] == "pass" for r in rows)
    assert os.path.basename(deps.__file__) == "deps.py"


def test_the_chat_loop_shows_the_card_and_the_model_never_sees_its_id():
    from puffpai import brain_loop, brain_tools, brain_tools_builtin
    brain_tools.clear()
    brain_tools_builtin.reset_for_tests()
    card = {"plan": "planid0123456789", "line": "Something it needs is missing.",
            "steps": ["The npm package x."], "manual": [], "keys": [], "thing": "skill-x"}
    brain_tools.register("try_it", "Try.", {"type": "object", "properties": {},
                                           "additionalProperties": False},
                         lambda a, c: {"worked": False, "note": "missing", "missing": dict(card)})

    class Brain:
        def __init__(self):
            self.calls = []
            self.replies = [{"content": "", "tool_calls": [
                {"function": {"name": "try_it", "arguments": {}}}]}, "It needs x."]

        def chat(self, messages, temperature=0.7, max_tokens=2048, tools=None):
            self.calls.append([dict(m) for m in messages])
            r = self.replies.pop(0)
            return r if isinstance(r, dict) else {"content": r, "tool_calls": []}
    brain, events = Brain(), []
    try:
        brain_loop.run([{"role": "user", "content": "go"}], ctx={}, backend=brain,
                       emit=events.append)
    finally:
        brain_tools.clear()
        brain_tools_builtin.reset_for_tests()
    shown = [e for e in events if e["type"] == "missing"]
    assert shown and shown[0]["card"]["plan"] == "planid0123456789"
    assert "planid0123456789" not in json.dumps(brain.calls)
