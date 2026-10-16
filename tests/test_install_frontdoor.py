"""The front door: getting BuildBot running without knowing what a terminal is.

What these guard, and why each one is silent for us and loud for a stranger:

* ``install.cmd`` is the double-click entry on Windows. A stock Windows client
  has an execution policy of Restricted, which blocks every ``.ps1`` - so the
  .cmd must hand ``install.ps1`` a process-only Bypass, and everything an owner
  double-clicks afterwards must be a ``.cmd`` as well.
* Missing tools are offered with winget after a plain yes, never installed
  silently, and without winget the owner gets a link.
* Owner-visible text is plain words: none of
  the wizard's invented lingo in anything the installers print.
* The README says what computer you need before it says how to download, in
  tiers that agree with config/model_catalog.json and model_profiles.json.

The real install (pip, npm, starting both services, stopping them by PID) is
proven by running install.cmd into a scratch folder, not here: it takes minutes
and downloads packages. These tests run the installer's dry run, which writes
nothing, and read the files.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from puffpai import plain_words  # noqa: E402  (the one banned list)

ROOT = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


# --- install.cmd ------------------------------------------------------------

def test_install_cmd_bypasses_the_policy_for_its_own_process_only() -> None:
    text = _read("install.cmd")
    assert re.search(r'powershell -NoProfile -ExecutionPolicy Bypass -File '
                     r'"%~dp0install\.ps1" %\*', text), (
        "install.cmd must run install.ps1 with a process-only Bypass and pass "
        "its options through.")
    # Never a machine- or user-wide change.
    assert "Set-ExecutionPolicy" not in text


def test_install_cmd_keeps_the_window_open_unless_told_not_to() -> None:
    text = _read("install.cmd")
    assert 'if not "%PUFFPAI_NO_PAUSE%"=="1" pause' in text
    assert "exit /b %RC%" in text


def test_install_cmd_has_crlf_line_endings_and_no_labels() -> None:
    """cmd.exe misreads some lines of an LF-only batch file; labels are where."""
    raw = (ROOT / "install.cmd").read_bytes()
    assert b"\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b""), (
        "install.cmd must use CRLF line endings throughout.")
    assert not re.search(rb"^\s*:", raw, re.M), "install.cmd grew a label."
    attrs = _read(".gitattributes")
    assert re.search(r"^\*\.cmd\s+text\s+eol=crlf", attrs, re.M)


# --- what an owner double-clicks --------------------------------------------

def _templates(name: str) -> str:
    return _read(name)


def test_every_windows_launcher_an_owner_clicks_is_a_cmd() -> None:
    text = _templates("install.ps1")
    for launcher in ("Start BuildBot.cmd", "Stop BuildBot.cmd",
                     "Set up BuildBot.cmd", "puffpai.cmd"):
        assert launcher in text
    # The two that need PowerShell hand over with Bypass; none calls a .ps1
    # bare, which a Restricted policy would block.
    for body in re.findall(r'\$(?:startCmd|stopCmd) = @"(.*?)"@', text, re.S):
        assert re.search(r'powershell -NoProfile -ExecutionPolicy Bypass -File '
                         r'"%~dp0run-(start|stop)\.ps1"', body), body


def test_start_waits_for_both_services_and_stop_goes_by_pid() -> None:
    text = _templates("install.ps1")
    assert '"http://127.0.0.1:$Port/health"' in text
    assert '($Url + "api/health")' in text
    assert "running.json" in text
    # Stop: only PIDs Start recorded, only if that process predates the record,
    # and its children with it.
    assert "StartTime -le $written" in text
    assert "taskkill.exe /PID $procId /T /F" in text


def test_nothing_listens_beyond_this_computer() -> None:
    for name in ("install.ps1", "install.sh"):
        text = _read(name)
        assert "0.0.0.0" not in text, f"{name} binds beyond loopback."
        assert "-H 127.0.0.1" in text


def test_linux_start_and_stop_mirror_windows() -> None:
    text = _read("install.sh")
    assert '"$REPO_DIR/start-bot.sh"' in text
    assert '"$REPO_DIR/stop-bot.sh"' in text
    assert "running.env" in text
    assert 'kill -- "-\\$pid"' in text  # the process group start-bot.sh made


def test_launcher_window_titles_carry_no_bot_name() -> None:
    """Start copies these files to 'Start <name>.cmd'; a fixed name in the
    title would then be the wrong one."""
    text = _templates("install.ps1")
    for var, title in (("startCmd", "title Start your bot"),
                       ("stopCmd", "title Stop your bot")):
        body = re.search(r'\$' + var + r' = @"(.*?)"@', text, re.S).group(1)
        assert title in body
        assert not re.search(r"^title .*BuildBot", body, re.M), body


# --- git is not needed ------------------------------------------------------
#
# dashboard/package-lock.json pins talkinghead to a git+ssh GitHub address.
# npm fetches that as a public https download (codeload.github.com): proven
# with npm 11.9 by running npm ci with git off the PATH and SSH
# disabled. So the installers must not ask a non-coder to install git, and must
# not refuse to build the web page without it.

def test_installers_do_not_ask_for_git() -> None:
    for name in ("install.ps1", "install.sh"):
        text = _read(name)
        assert "Git.Git" not in text, name
        assert "git-scm.com" not in text, name
        assert not re.search(r'Have "git"|command -v git', text), name
        for line in _owner_visible_lines(name):
            assert not re.search(r"\bgit\b", line), (name, line)


# --- plain words --------------------------------------------------------------

#: The banned words: none of these may reach a person's screen.
#: The owner's list lives in puffpai.plain_words; installers also never
#: name the security agent's internal setting.
JARGON_EXTRA = ("drawbridge",)


def _owner_visible_lines(name: str) -> list[str]:
    """Every line that prints something, minus comments."""
    printers = ("Say", "Write-Host", "Step ", "Fail ", "Read-Host",
                "Ask-YesNo", "say ", "step ", "fail ", "echo ", "printf",
                "ask_yes_no")
    out = []
    in_help = False
    for line in _read(name).splitlines():
        bare = line.strip()
        if bare.startswith(("#", "rem ")):
            continue
        if bare.startswith(("@\"", "cat <<'EOF'")) and not in_help:
            in_help = True
        elif in_help and bare in ('"@ | Write-Host', "EOF"):
            in_help = False
        if in_help or any(p in bare for p in printers):
            out.append(bare)
    return out


@pytest.mark.parametrize("name", ("install.ps1", "install.sh", "install.cmd"))
def test_installers_speak_plain_words(name: str) -> None:
    lines = _owner_visible_lines(name)
    assert lines, f"found nothing printed in {name}?"
    for line in lines:
        assert plain_words.is_plain(line, also=JARGON_EXTRA), (
            f"{name} shows {plain_words.check(line, also=JARGON_EXTRA)} "
            f"to a person")


def test_readme_install_section_speaks_plain_words() -> None:
    text = _read("README.md")
    top = text[text.index("**What computer you need**"):text.index("## The wizard")]
    assert plain_words.is_plain(top, also=JARGON_EXTRA), (
        f"README's install section says "
        f"{plain_words.check(top, also=JARGON_EXTRA)}.")


# --- the dry run, for real --------------------------------------------------

POWERSHELL = shutil.which("powershell") if sys.platform == "win32" else None


def _dry_run(tmp_path: Path, pretend: str) -> str:
    env = dict(os.environ)
    env["PUFFPAI_INSTALL_PRETEND_MISSING"] = pretend
    env["OLLAMA_HOST"] = "http://127.0.0.1:9"   # nothing answers there
    for name in ("PUFFPAI_STATE_DIR", "PUFFPAI_DATA_DIR"):
        env.pop(name, None)
    state = tmp_path / "state"
    proc = subprocess.run(
        [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
         str(ROOT / "install.ps1"), "-DryRun", "-NoQuestions",
         "-Port", "18248", "-DashboardPort", "13248",
         "-StateDir", str(state)],
        capture_output=True, text=True, env=env, timeout=180,
        stdin=subprocess.DEVNULL)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not state.exists(), "a dry run wrote the state dir."
    return proc.stdout


needs_powershell = pytest.mark.skipif(
    POWERSHELL is None, reason="Windows PowerShell is needed for install.ps1")


@needs_powershell
def test_dry_run_offers_winget_for_every_missing_tool(tmp_path) -> None:
    out = _dry_run(tmp_path, "python,node,ollama")
    for winget_id in ("Python.Python.3.12", "Ollama.Ollama",
                      "OpenJS.NodeJS.LTS"):
        assert f"winget install --id {winget_id} -e" in out, winget_id
    assert out.count("[dry-run] would ask: Install ") == 3
    assert "Practice run finished. Nothing was changed." in out
    # Nothing new at the repo root either.
    for launcher in ("Start BuildBot.cmd", "run-start.ps1", "puffpai.cmd"):
        assert not (ROOT / launcher).exists() or launcher in _git_tracked()


@needs_powershell
def test_without_winget_the_owner_gets_a_link_not_a_command(tmp_path) -> None:
    out = _dry_run(tmp_path, "python,node,ollama,winget")
    assert "winget install" not in out
    for page in ("https://www.python.org/downloads/",
                 "https://ollama.com/download/windows", "https://nodejs.org"):
        assert page in out, page


@needs_powershell
def test_dry_run_output_is_plain_words(tmp_path) -> None:
    out = _dry_run(tmp_path, "")
    shown = [ln for ln in out.splitlines()
             if ln.strip() and not ln.startswith("      ")   # file bodies
             and "[dry-run]" not in ln]                       # commands
    assert any(ln.startswith("Step 6 of 6") for ln in shown)
    for line in shown:
        assert plain_words.is_plain(line, also=JARGON_EXTRA), line


def _git_tracked() -> str:
    try:
        return subprocess.run(["git", "-C", str(ROOT), "ls-files"],
                              capture_output=True, text=True).stdout
    except OSError:
        return ""


BASH = shutil.which("bash")
if BASH and "system32" in BASH.lower():
    BASH = None     # Windows' Linux launcher: it would start her own distro


@pytest.mark.skipif(BASH is None, reason="bash is not installed")
def test_install_sh_parses() -> None:
    proc = subprocess.run([BASH, "-n", str(ROOT / "install.sh")],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


def _sh_dry_run(tmp_path: Path, pretend: str) -> subprocess.CompletedProcess:
    """install.sh --dry-run with this test's own Python first on PATH."""
    shim = tmp_path / "shim"
    shim.mkdir()
    (shim / "python3").write_text(
        f'#!/bin/sh\nexec "{Path(sys.executable).as_posix()}" "$@"\n',
        encoding="utf-8", newline="\n")
    (shim / "python3").chmod(0o755)
    env = dict(os.environ)
    env["PATH"] = str(shim) + os.pathsep + env.get("PATH", "")
    env["PUFFPAI_INSTALL_PRETEND_MISSING"] = pretend
    env["OLLAMA_HOST"] = "http://127.0.0.1:9"   # nothing answers there
    # The no-download paths (a computer with no pinned build). The download
    # offer itself is tests/test_install_tools_sh.py's.
    env["PUFFPAI_TOOLS_PLATFORM"] = "unsupported"
    env["PUFFPAI_TOOLS_DIR"] = (tmp_path / "tools").as_posix()   # never this computer's
    for name in ("PUFFPAI_STATE_DIR", "PUFFPAI_DATA_DIR"):
        env.pop(name, None)
    state = tmp_path / "state"
    proc = subprocess.run(
        [BASH, str(ROOT / "install.sh"), "--dry-run", "--no-questions",
         "--port", "18249", "--dashboard-port", "13249",
         "--state-dir", state.as_posix()],
        capture_output=True, text=True, env=env, timeout=300,
        stdin=subprocess.DEVNULL)
    assert not state.exists(), "a dry run wrote the state dir."
    return proc


@pytest.mark.skipif(BASH is None, reason="bash is not installed")
def test_install_sh_stops_plainly_without_python(tmp_path) -> None:
    proc = _sh_dry_run(tmp_path, "python")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "BuildBot needs Python 3.11 or newer" in proc.stderr
    assert "then run this setup again" in proc.stderr


@pytest.mark.skipif(BASH is None, reason="bash is not installed")
def test_install_sh_goes_on_without_ollama_or_node(tmp_path) -> None:
    proc = _sh_dry_run(tmp_path, "node,ollama")
    out = proc.stdout
    assert proc.returncode == 0, out + proc.stderr
    assert "Ollama is not on this computer" in out
    assert "https://nodejs.org" in out
    assert "Ollama is not installed yet" in out
    assert "Practice run finished. Nothing was changed." in out


# --- the README's hardware box ----------------------------------------------

def _box() -> str:
    text = _read("README.md")
    start = text.index("**What computer you need**")
    end = text.index("## Install", start)
    return text[start:end]


def test_readme_says_what_computer_you_need_before_how_to_download() -> None:
    text = _read("README.md")
    box = text.index("**What computer you need**")
    for first in ("git clone", "Download ZIP", "install.cmd"):
        assert box < text.index(first), f"{first!r} comes before the box."


def test_readme_hardware_box_is_plain_and_complete() -> None:
    box = _box()
    for needle in ("8 GB", "16 GB", "32 GB", "Windows 10 or 11", "Linux",
                   "graphics card", "Internet"):
        assert needle in box, needle
    assert "!" not in box, "no exclamation marks."


def test_readme_memory_tiers_agree_with_the_model_catalog() -> None:
    """8 GB must fit a one-job-at-a-time brain, 16 GB the own-words one (the
    9B tool model: "9B or larger where the
    computer can run it")."""
    catalog = json.loads(_read("config/model_catalog.json"))
    profiles = json.loads(_read("config/model_profiles.json"))["profiles"]
    models = catalog.get("models", catalog)
    rows = models if isinstance(models, list) else list(models.values())

    def floor(routing: str) -> int:
        fits = [m["min_ram_gb"] for m in rows
                if profiles.get(m["id"], {}).get("routing_tier") == routing]
        return min(fits)

    assert floor("guided") <= 8, "no one-job-at-a-time brain fits 8 GB."
    assert floor("light") <= 32, "the own-words brain does not fit 32 GB."
    assert floor("light") <= 16, "16 GB must reach the 9B tool model; fix the box."
    assert floor("light") > 8, "8 GB would reach an own-words brain; fix the box."
    assert floor("rails") <= 2 and "2 GB" in _box()
