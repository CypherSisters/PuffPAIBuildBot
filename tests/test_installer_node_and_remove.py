"""Installer changes: the Node.js floor, the node path
record, the locked-box line, and the Remove BuildBot files.

The Node floor is tested by running each installer's own check function
(PowerShell's Test-NodeVersion, bash's node_ok) against a stand-in ``node``
that prints a chosen version; nothing is installed or downloaded.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from puffpai import plain_words
from puffpai.box import wording as W

ROOT = Path(__file__).resolve().parents[1]
POWERSHELL = (os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32",
                           "WindowsPowerShell", "v1.0", "powershell.exe")
              if sys.platform == "win32" else None)
BASH = shutil.which("bash")
if BASH and "system32" in BASH.lower():
    BASH = None     # that is Windows' Linux launcher: it would start her own distro


def _read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def _block(text: str, start: str) -> str:
    """A PowerShell function or here-string from install.ps1, by its first line."""
    i = text.index(start)
    if start.startswith("function"):
        depth, j = 0, text.index("{", i)
        for k in range(j, len(text)):
            depth += {"{": 1, "}": -1}.get(text[k], 0)
            if depth == 0:
                return text[i:k + 1]
    raise AssertionError(start)


# ── the Node.js floor ────────────────────────────────────────────────────────

def test_node_minimum_is_22_4_in_both_installers():
    ps1, sh = _read("install.ps1"), _read("install.sh")
    assert "$NodeMinMajor = 22" in ps1 and "$NodeMinMinor = 4" in ps1
    assert "NODE_MIN_MAJOR=22" in sh and "NODE_MIN_MINOR=4" in sh
    assert "20.19 or newer" not in ps1 and "20.19 or newer" not in sh
    assert "needs 22.12 or newer" in ps1 and "22.12 or newer" in sh


CASES = [("v20.19.0", False), ("v20.20.1", False), ("v22.3.0", False), ("v22.4.0", False),
         ("v22.11.9", False), ("v22.12.0", True), ("v23.0.0", True), ("v24.14.0", True),
         ("garbage", False)]


@pytest.mark.skipif(POWERSHELL is None or not os.path.isfile(POWERSHELL or ""),
                    reason="Windows PowerShell")
def test_install_ps1_node_check_refuses_node_without_websocket():
    ps1 = _read("install.ps1")
    fn = _block(ps1, "function Test-NodeVersion")
    floors = "\n".join(line for line in ps1.splitlines()
                       if line.startswith(("$NodeMinMajor", "$NodeMinMinor", "$NodeWebPageMinor")))
    lines = [floors, fn]
    for version, _ok in CASES:
        lines.append(f'function node {{ "{version}" }}; Write-Output ("{version}=" + (Test-NodeVersion))')
    proc = subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", "\n".join(lines)],
                          capture_output=True, text=True, timeout=120)
    got = dict(line.split("=", 1) for line in proc.stdout.split() if "=" in line)
    for version, ok in CASES:
        assert got.get(version) == str(ok), (version, proc.stdout, proc.stderr)


@pytest.mark.skipif(BASH is None, reason="bash is not installed")
def test_install_sh_node_check_refuses_node_without_websocket():
    sh = _read("install.sh")
    fn = re.search(r"^node_ok\(\) \{.*?^\}", sh, re.S | re.M).group(0)
    floors = "\n".join(line for line in sh.splitlines()
                       if line.startswith(("NODE_MIN_MAJOR=", "NODE_MIN_MINOR=",
                                           "NODE_WEB_PAGE_MINOR=")))
    script = [floors, "have() { return 0; }", fn]
    for version, _ok in CASES:
        script.append(f'node() {{ echo "{version}"; }}; if node_ok; then echo "{version}=True"; '
                      f'else echo "{version}=False"; fi')
    proc = subprocess.run([BASH, "-c", "\n".join(script)], capture_output=True, text=True,
                          timeout=60)
    got = dict(line.split("=", 1) for line in proc.stdout.split() if "=" in line)
    for version, ok in CASES:
        assert got.get(version) == str(ok), (version, proc.stdout, proc.stderr)


def test_both_installers_record_the_node_path():
    assert "node_path.txt" in _read("install.ps1")
    assert "node_path.txt" in _read("install.sh")
    ps1 = _read("install.ps1")
    assert "[System.IO.Path]::GetFullPath($nodeExe)" in ps1


def test_node_path_is_written_even_without_the_web_page():
    """The app runner needs node_path.txt whether or not the web page is
    built (-NoDashboard): the record must be written in both branches."""
    ps1 = _read("install.ps1")
    i = ps1.index("if (-not $BuildDashboard) {")
    j = ps1.index("} else {", i)
    assert "Write-NodePath" in ps1[i:j]
    k = ps1.index("npm.cmd ci", j)
    assert "Write-NodePath" in ps1[j:k]
    # Only one place writes the file: the function.
    assert ps1.count('Join-Path $RepoDir "node_path.txt"') == 1


def _run_write_node_path(tmp_path, version, *, have=True, stale=False, dry=False):
    ps1 = _read("install.ps1")
    floors = "\n".join(line for line in ps1.splitlines()
                       if line.startswith(("$NodeMinMajor", "$NodeMinMinor")))
    fake = tmp_path / "nodejs" / "node.exe"
    fake.parent.mkdir(exist_ok=True)
    fake.write_text("stand-in", encoding="ascii")
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    record = repo / "node_path.txt"
    if stale:
        record.write_text(r"C:\old\node.exe", encoding="ascii")
    script = [
        floors,
        f"$RepoDir = '{repo}'",
        f"$DryRun = ${'true' if dry else 'false'}",
        "function Say ($m) { Write-Output $m }",
        "function Pretends ($n) { return $false }",
        f"function Get-Command {{ param($n, $ErrorAction) "
        f"if (${'true' if have else 'false'}) {{ [pscustomobject]@{{ Source = '{fake}' }} }} }}",
        f'function node {{ "{version}" }}',
        _block(ps1, "function Have"),
        _block(ps1, "function Test-NodeRunner"),
        _block(ps1, "function Write-NodePath"),
        "Write-NodePath",
    ]
    proc = subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-Command",
                           "\n".join(script)], capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    return record, fake, proc.stdout


@pytest.mark.skipif(POWERSHELL is None or not os.path.isfile(POWERSHELL or ""),
                    reason="Windows PowerShell")
def test_write_node_path_records_the_full_path_for_a_new_enough_node(tmp_path):
    record, fake, _out = _run_write_node_path(tmp_path, "v22.4.0")
    assert record.read_text(encoding="ascii").strip() == str(fake)


@pytest.mark.skipif(POWERSHELL is None or not os.path.isfile(POWERSHELL or ""),
                    reason="Windows PowerShell")
@pytest.mark.parametrize("version,have", [("v22.3.9", True), ("v20.19.0", True),
                                          ("garbage", True), ("v24.0.0", False)])
def test_write_node_path_leaves_no_record_for_old_or_missing_node(tmp_path, version, have):
    record, _fake, _out = _run_write_node_path(tmp_path, version, have=have, stale=True)
    assert not record.exists()


@pytest.mark.skipif(POWERSHELL is None or not os.path.isfile(POWERSHELL or ""),
                    reason="Windows PowerShell")
def test_write_node_path_dry_run_writes_nothing(tmp_path):
    record, _fake, out = _run_write_node_path(tmp_path, "v24.1.0", dry=True)
    assert not record.exists()
    assert "[dry-run] would write the node program's place" in out


def test_both_installers_mention_the_locked_box_later():
    for name in ("install.ps1", "install.sh"):
        assert W.INSTALL_BOX_LATER in _read(name), name


# ── Remove BuildBot files ────────────────────────────────────────────────────

def test_install_ps1_writes_remove_buildbot_files():
    ps1 = _read("install.ps1")
    assert 'Write-Launcher (Join-Path $RepoDir "remove-buildbot.ps1") (Expand-RemoveTemplate $removeTemplate)' in ps1
    # .ps1 launchers are UTF-8 with a byte order mark (a folder name with
    # accents must survive); .cmd files stay ASCII.
    fn = _block(ps1, "function Write-Launcher")
    assert 'if ($path -like "*.ps1")' in fn and "UTF8Encoding($true)" in fn
    assert 'Write-Launcher (Join-Path $RepoDir "Remove BuildBot.cmd") (Expand-Template $removeCmd)' in ps1
    assert '@("Remove BuildBot.cmd", ' in ps1


def test_remove_buildbot_cmd_runs_a_copy_from_temp():
    body = re.search(r'\$removeCmd = @"(.*?)"@', _read("install.ps1"), re.S).group(1)
    assert r'copy /y "%~dp0remove-buildbot.ps1" "%PUFFPAI_REMOVE_PS1%"' in body
    assert r'set "PUFFPAI_REMOVE_PS1=%TEMP%\puffpai-remove-' in body
    # Out of the folder before anything runs, by absolute path, with the
    # install folder handed over (the trailing "." keeps the quote intact).
    # It leaves the BuildBot folder BEFORE setlocal, so ending the batch never
    # tries to go back into a folder that was just deleted.
    assert body.index('cd /d "%TEMP%"') < body.index("setlocal")
    assert '"%SystemRoot%\\System32\\WindowsPowerShell\\v1.0\\powershell.exe"' in body
    # A plain "exit" (this window's own cmd): "exit /b" makes cmd read the
    # batch file again after it was deleted, which printed "The system cannot
    # find the path specified." twice and lost the exit code (measured).
    assert body.rstrip().endswith('-ExecutionPolicy Bypass -File "%PUFFPAI_REMOVE_PS1%" '
                                  '-InstallRoot "%~dp0." %* & exit')
    # The script's own exit code is the .cmd's: nothing runs after powershell
    # (the script deletes its own temp copy).
    assert "del " not in body
    assert "title Remove BuildBot" in body
    assert not re.search(r"^\s*:", body, re.M), "a batch label"


def _remove_template() -> str:
    return re.search(r"\$removeTemplate = @'\r?\n(.*?)\r?\n'@", _read("install.ps1"),
                     re.S).group(1)


def test_remove_script_never_follows_links_or_kills_by_command_line():
    t = _remove_template()
    assert "Get-ChildItem -LiteralPath $dir -Force -Attributes !ReparsePoint" in t
    assert "Get-ChildItem -LiteralPath $dir -Force -Attributes ReparsePoint" in t
    assert "[System.IO.Directory]::Delete($item.FullName, $false)" in t
    assert "[System.IO.File]::Delete($item.FullName)" in t
    assert "Remove-Item -Recurse" not in t and "-Recurse" not in t
    assert "CommandLine" not in t and "cmdline" not in t.lower()
    assert "& $TaskKill /PID $id /T /F" in t
    assert "Is-Under $exe $Repo" in t
    assert "StartTime -le $written" in t
    assert "AddSeconds(30)" in t and "Start-Sleep -Seconds 1" in t
    assert "--shutdown" not in t and "wsl" not in t.lower().replace("wslconfig", "")
    # The box and the saved entries go only through BuildBot's own guard;
    # the saved entries after the vault folder.
    assert "-m puffpai.remove_cli --only box --yes" in t
    assert "-m puffpai.remove_cli --only keyring --yes" in t
    # Python's refusals stop the script too.
    assert "foreach ($Refused in @($Facts.refused))" in t


def test_remove_script_paths_are_single_quoted_literals():
    t = _remove_template()
    for name in ("REPO", "STATE", "DATA", "ENVFILE"):
        assert f"'__RM_{name}__'" in t
    fn = _read("install.ps1")
    assert 'Replace("\'", "\'\'")' in fn


def test_remove_script_refuses_the_dangerous_roots():
    t = _remove_template()
    for folder in ("UserProfile", "Desktop", "MyDocuments", "MyPictures", "LocalApplicationData",
                   "Windows", "ProgramFiles"):
        assert f'"{folder}"' in t, folder
    assert "GetPathRoot" in t
    assert "[System.IO.FileAttributes]::ReparsePoint" in t


def test_remove_script_words_match_the_python_side():
    t = _remove_template()
    for line in (W.REMOVE_TITLE, W.REMOVE_INTRO, W.REMOVE_VAULT, W.REMOVE_VAULT_ASK,
                 W.REMOVE_OWN_FILES, W.REMOVE_CONFIRM, W.REMOVE_KEPT, W.REMOVE_NOTHING,
                 W.REMOVE_FINISHED, W.REMOVE_NOT_CHECKED, W.REMOVE_VAULT_COPY_FAILED,
                 W.REMOVE_WRONG_FOLDER, W.REMOVE_CLOSE, W.REMOVE_KEY_WINDOWS.strip()):
        assert line in t, line
    for tmpl in (W.REMOVE_FOLDER, W.REMOVE_STATE, W.REMOVE_APPS, W.REMOVE_BOX,
                 W.REMOVE_LOCKED_WINDOWS, W.REMOVE_REFUSED, W.REMOVE_DATA_STAYS,
                 W.REMOVE_APPS_STAY, W.REMOVE_VAULT_COPIED):
        head = tmpl.split("{")[0]
        assert head in t, head
    for why in (W.REMOVE_WHY_DRIVE, W.REMOVE_WHY_MAIN_FOLDER, W.REMOVE_WHY_LINK,
                W.REMOVE_WHY_NOT_BUILDBOT):
        assert f'"{why}"' in t, why


@pytest.mark.skipif(POWERSHELL is None or not os.path.isfile(POWERSHELL or ""),
                    reason="Windows PowerShell")
def test_install_ps1_and_the_generated_script_parse(tmp_path):
    script = tmp_path / "remove-buildbot.ps1"
    script.write_text(_remove_template(), encoding="ascii")
    check = ("$t=$null;$e=$null;foreach($f in $args){[void][System.Management.Automation."
             "Language.Parser]::ParseFile($f,[ref]$t,[ref]$e);if($e.Count){$e|%{Write-Output "
             "(\"ERR \"+$f+\": \"+$_.Message)}}}")
    proc = subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-Command",
                           f"& {{ {check} }} '{ROOT / 'install.ps1'}' '{script}'"],
                          capture_output=True, text=True, timeout=120)
    assert "ERR" not in proc.stdout, proc.stdout


def test_install_sh_has_a_remove_command():
    sh = _read("install.sh")
    assert 'if [ "${1:-}" = "remove" ]; then' in sh
    assert 'exec "$RM_PY" -m puffpai.remove_cli "$@"' in sh
    assert 'cd "${TMPDIR:-/tmp}"' in sh
    assert "bash install.sh remove" in sh


def test_new_installer_lines_are_plain():
    for line in (W.INSTALL_BOX_LATER, "takes BuildBot off this computer (it asks first)",
                 "The removal file is missing from this folder. Run install.cmd again, "
                 "then try once more."):
        assert plain_words.is_plain(line), plain_words.check(line)
        assert "\u2014" not in line


def test_package_data_ships_the_box_and_runner_files():
    setup = _read("setup.py")
    for pattern in ('"box/assets/*"', '"webapps/runner/*.mjs"', '"webapps/runner/shim.js"'):
        assert pattern in setup, pattern
    manifest = _read("MANIFEST.in")
    assert "recursive-include src/puffpai/box/assets *" in manifest
    assert "recursive-include src/puffpai/webapps/runner *.mjs shim.js" in manifest


# ── every owner-facing string the installers add ────────────────────────────────

_L5_BANNED = re.compile(r"(?i)\bdoors?\b|\u2014|\bbeta\b|\bwart|sandbox|egress|\brepo |SPDX|"
                        r"manifest|MiniLM|embedding|seccomp|bwrap|WSL2|little app|parts\)|"
                        r"\b0 parts|recipe check|recipe file|" + "Beth" + "any")
_API = re.compile(r"(?<![\w.])API(?![\w.])")
_HEX = re.compile(r"(?<![0-9A-Za-z_])[0-9a-fA-F]{7,40}(?![0-9A-Za-z_])")


def test_every_l7_owner_string_is_plain():
    lines = (ROOT / "tests" / "fixtures" / "ui_strings" / "locked_box_install.txt").read_text(
        encoding="utf-8").splitlines()
    assert len(lines) >= 50
    for tmpl in W.ALL:
        for part in tmpl.splitlines():
            if part.strip():
                assert part in lines, f"{part!r} is missing from locked_box_install.txt"
    for line in lines:
        filled = re.sub(r"\{[a-z]+\}", "x", line)
        assert plain_words.is_plain(filled), plain_words.check(filled)
        assert not _L5_BANNED.search(filled), line
        assert not _API.search(filled), line
        assert not _HEX.search(filled), line



def _remove_cmd_body() -> str:
    body = re.search(r'\$removeCmd = @"\r?\n(.*?)\r?\n"@', _read("install.ps1"), re.S).group(1)
    return body.replace("$Marker", "Generated by a test")


@pytest.mark.skipif(POWERSHELL is None or not os.path.isfile(POWERSHELL or ""),
                    reason="Windows PowerShell")
@pytest.mark.parametrize("code", [0, 1, 2])
def test_remove_buildbot_cmd_reports_the_script_exit_code_cleanly(tmp_path, code):
    """Review findings: the .cmd always returned 0, and after a real removal
    cmd printed "The system cannot find the path specified." twice."""
    inst = tmp_path / "Scratch BuildBot"
    inst.mkdir()
    (inst / "Remove BuildBot.cmd").write_text(_remove_cmd_body().replace("\n", "\r\n"),
                                              encoding="ascii")
    stub = ("param([string]$InstallRoot = '')\r\n"
            "Write-Host ('stub ran for ' + $InstallRoot)\r\n"
            f"if ({code} -eq 0) {{ Remove-Item -LiteralPath $InstallRoot -Recurse -Force }}\r\n"
            "Remove-Item -LiteralPath $PSCommandPath -Force\r\n"
            f"exit {code}\r\n")
    (inst / "remove-buildbot.ps1").write_text(stub, encoding="utf-8-sig")
    env = dict(os.environ, PUFFPAI_NO_PAUSE="1", TEMP=str(tmp_path), TMP=str(tmp_path))
    cmd = os.path.join(os.environ["SystemRoot"], "System32", "cmd.exe")
    proc = subprocess.run([cmd, "/d", "/c", "call", str(inst / "Remove BuildBot.cmd")],
                          capture_output=True, text=True, timeout=120, env=env,
                          cwd=str(inst))
    assert "stub ran for" in proc.stdout, proc.stdout + proc.stderr
    assert proc.returncode == code, proc.stdout + proc.stderr
    assert "cannot find the path" not in (proc.stdout + proc.stderr).lower()
    assert inst.exists() == (code != 0)
    assert not list(tmp_path.glob("puffpai-remove-*.ps1"))
