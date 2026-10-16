"""scripts/install_tools.sh: the pinned, checked downloads a green computer
gets after one yes (Python, Node.js, Ollama on a Mac), and the Mac launchers.

Every test runs the real shell functions with bash, with the network replaced
by a local file (tools_fetch is redefined), so nothing is downloaded. Runs on
Linux and macOS CI, and on Windows with Git's bash (never Windows' own
bash.exe, which would start a WSL distro).
"""
from __future__ import annotations

import hashlib
import io
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

from puffpai import plain_words

ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "scripts" / "install_tools.sh"
BASH = shutil.which("bash")
if BASH and "system32" in BASH.lower():
    BASH = None     # Windows' Linux launcher: it would start her own distro
needs_bash = pytest.mark.skipif(BASH is None, reason="bash is not installed")

PLATFORMS = ("darwin-arm64", "darwin-x64", "linux-x64", "linux-arm64")
ALLOWED = ("https://github.com/astral-sh/python-build-standalone/releases/download/",
           "https://github.com/ollama/ollama/releases/download/",
           "https://nodejs.org/dist/")


def _posix(p) -> str:
    """A full path as bash reads it. Git's bash on Windows wants /c/... (a
    "C:" path reads as relative to install.sh, and as another computer to tar)."""
    text = Path(p).as_posix()
    if os.name == "nt" and len(text) > 2 and text[1] == ":":
        text = "/" + text[0].lower() + text[2:]
    return text


def sh(script: str, *, env: dict | None = None, cwd=None) -> subprocess.CompletedProcess:
    """Run *script* in bash with the helper sourced first."""
    full = f'set -u\n. "{_posix(LIB)}"\n{script}\n'
    run_env = dict(os.environ)
    run_env.pop("PUFFPAI_TOOLS_PLATFORM", None)
    run_env.pop("PUFFPAI_TOOLS_DIR", None)
    run_env.update(env or {})
    return subprocess.run([BASH, "-c", full], capture_output=True, text=True,
                          env=run_env, cwd=cwd, timeout=120, stdin=subprocess.DEVNULL)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _targz(path: Path, members: dict[str, bytes], *, modes: dict | None = None) -> Path:
    with tarfile.open(path, "w:gz") as tf:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = (modes or {}).get(name, 0o644)
            tf.addfile(info, io.BytesIO(data))
    return path


# -- the pins ----------------------------------------------------------------

def _text() -> str:
    return LIB.read_text(encoding="utf-8")


def test_the_helper_is_ascii_and_lf_only():
    raw = LIB.read_bytes()
    assert b"\r\n" not in raw
    assert not sorted({ch for ch in raw.decode("utf-8") if ord(ch) > 127})


def test_every_address_is_https_on_one_of_the_three_release_sites():
    urls = re.findall(r"https?://[^\s\"']+", _text())
    assert urls
    for url in urls:
        assert url.startswith(ALLOWED), url


def test_no_sudo_no_pipe_to_a_shell_and_https_only_transfers():
    code = "\n".join(line for line in _text().splitlines()
                     if not line.lstrip().startswith("#"))
    assert "sudo" not in code
    assert not re.search(r"\|\s*(ba)?sh\b", code)
    assert "--proto '=https'" in code and "--proto-redir '=https'" in code
    assert "--https-only" in code


@needs_bash
@pytest.mark.parametrize("platform", PLATFORMS)
@pytest.mark.parametrize("tool", ("python", "node", "ollama"))
def test_each_pin_is_one_https_address_and_one_sha256(platform, tool):
    got = sh(f"tools_pin {tool} {platform}")
    if tool == "ollama" and platform.startswith("linux"):
        assert got.returncode != 0 and got.stdout == ""      # Linux: Ollama's own script
        return
    assert got.returncode == 0, got.stderr
    url, sha = got.stdout.split()
    assert url.startswith(ALLOWED)
    assert re.fullmatch(r"[0-9a-f]{64}", sha)
    assert {"python": "3.12.15", "node": "24.21.0", "ollama": "0.40.2"}[tool] in url


@needs_bash
def test_no_pin_for_an_unknown_computer():
    for tool in ("python", "node", "ollama"):
        assert sh(f"tools_pin {tool} unsupported").returncode != 0
        assert sh(f"tools_pin {tool} windows-x64").returncode != 0


@needs_bash
def test_the_platform_override_is_honoured_and_defaults_are_named():
    got = sh("tools_platform", env={"PUFFPAI_TOOLS_PLATFORM": "darwin-x64"})
    assert got.stdout.strip() == "darwin-x64"
    real = sh("tools_platform").stdout.strip()
    assert real in PLATFORMS + ("unsupported",)


# -- the checks --------------------------------------------------------------

@needs_bash
def test_verify_takes_only_the_exact_hash(tmp_path):
    f = tmp_path / "file.bin"
    f.write_bytes(b"BuildBot" * 1000)
    good = _sha(f)
    script = (f'f="{_posix(f)}"\n'
              f'tools_verify "$f" {good} && echo GOOD\n'
              f'tools_verify "$f" {"0" * 64} || echo WRONG\n'
              f'tools_verify "$f" {good.upper()} || echo UPPER\n'
              f'tools_verify "$f" {good[:-1]} || echo SHORT\n'
              f'tools_verify "$f" "" || echo EMPTY\n'
              f'tools_verify "$f.missing" {good} || echo MISSING\n')
    out = sh(script).stdout.split()
    assert out == ["GOOD", "WRONG", "UPPER", "SHORT", "EMPTY", "MISSING"]


@needs_bash
@pytest.mark.parametrize("url", [
    "http://nodejs.org/dist/v24.21.0/node.tar.gz",
    "https://evil.example.org/node.tar.gz",
    "https://github.com/someone-else/python/releases/download/x.tar.gz",
    "https://nodejs.org.evil.example/dist/x.tar.gz",
    "ftp://nodejs.org/dist/x.tar.gz",
])
def test_fetch_refuses_any_other_address_before_dialing(tmp_path, url):
    out = tmp_path / "x"
    got = sh(f'tools_fetch "{url}" "{_posix(out)}"; echo "rc=$?"')
    assert "rc=1" in got.stdout
    assert not out.exists()


@needs_bash
def test_a_download_that_does_not_match_is_deleted_and_never_kept(tmp_path):
    src = tmp_path / "served.tar.gz"
    src.write_bytes(b"not what was pinned")
    out = tmp_path / "got.tar.gz"
    script = (f'tools_fetch() {{ cp "{_posix(src)}" "$2"; }}\n'
              f'tools_fetch_verified https://nodejs.org/dist/x {"a" * 64} "{_posix(out)}"; '
              'echo "rc=$?"')
    got = sh(script)
    assert "rc=1" in got.stdout
    assert "did not match the copy BuildBot expects" in got.stdout
    assert not out.exists() and not Path(str(out) + ".part").exists()


@needs_bash
def test_a_download_that_matches_is_kept(tmp_path):
    src = tmp_path / "served.tar.gz"
    src.write_bytes(b"exactly the pinned bytes")
    out = tmp_path / "got.tar.gz"
    script = (f'tools_fetch() {{ cp "{_posix(src)}" "$2"; }}\n'
              f'tools_fetch_verified https://nodejs.org/dist/x {_sha(src)} "{_posix(out)}"; '
              'echo "rc=$?"')
    assert "rc=0" in sh(script).stdout
    assert out.read_bytes() == src.read_bytes()


@needs_bash
@pytest.mark.parametrize("bad", ["../escape.txt", "python/../../escape.txt", "/abs.txt"])
def test_an_archive_that_climbs_out_is_refused_before_unpacking(tmp_path, bad):
    arc = _targz(tmp_path / "a.tar.gz", {"python/ok.txt": b"ok", bad: b"bad"})
    dest = tmp_path / "x"
    got = sh(f'tools_unpack "{_posix(arc)}" "{_posix(dest)}"; echo "rc=$?"')
    assert "rc=1" in got.stdout
    assert not dest.exists()
    assert not (tmp_path / "escape.txt").exists()


@needs_bash
def test_unpacking_needs_a_new_folder(tmp_path):
    arc = _targz(tmp_path / "a.tar.gz", {"python/ok.txt": b"ok"})
    dest = tmp_path / "x"
    assert "rc=0" in sh(f'tools_unpack "{_posix(arc)}" "{_posix(dest)}"; echo "rc=$?"').stdout
    assert (dest / "python" / "ok.txt").read_bytes() == b"ok"
    again = sh(f'tools_unpack "{_posix(arc)}" "{_posix(dest)}"; echo "rc=$?"')
    assert "rc=1" in again.stdout


@needs_bash
def test_removing_refuses_anything_outside_the_tools_folder(tmp_path):
    tools = tmp_path / "tools"
    (tools / "own").mkdir(parents=True)
    keep = tmp_path / "keep"
    keep.mkdir()
    t = _posix(tools)
    got = sh(f'tools_remove_own "{t}" "{_posix(keep)}" || echo R1\n'
             f'tools_remove_own "{t}" "{t}" || echo R2\n'
             f'tools_remove_own "{t}" "{t}/" || echo R3\n'
             f'tools_remove_own "{t}" "{t}/own/../.." || echo R4\n'
             f'tools_remove_own "" "{_posix(keep)}" || echo R5\n'
             f'tools_remove_own "{t}" "{t}/own" && echo OK\n')
    assert got.stdout.split() == ["R1", "R2", "R3", "R4", "R5", "OK"]
    assert keep.is_dir() and tools.is_dir() and not (tools / "own").exists()


@needs_bash
def test_the_tools_folder_may_not_sit_inside_a_bot_folder(tmp_path):
    repo, state, apps = (_posix(tmp_path / n) for n in ("repo", "state", "apps"))
    base = _posix(tmp_path)
    cases = {f"{base}/tools": "OK", f"{repo}/tools": "NO",
             f"{state}/tools": "NO", apps: "NO", "relative/tools": "NO",
             f"{base}/a/../repo/tools": "NO",
             f"{state}-tools": "OK"}
    for tools, want in cases.items():
        got = sh(f'tools_dir_ok "{tools}" "{repo}" "{state}" "{apps}" && echo OK || echo NO')
        assert got.stdout.strip() == want, (tools, got.stdout)


@needs_bash
def test_the_default_tools_folder_is_beside_the_private_files_not_inside():
    got = sh("tools_default_dir", env={"HOME": "/home/x", "XDG_DATA_HOME": ""})
    if sys.platform == "darwin":
        assert got.stdout.strip() == "/home/x/Library/Application Support/PuffPAI-tools"
    else:
        assert got.stdout.strip() == "/home/x/.local/share/puffpai-tools"
    assert sh("tools_default_dir", env={"PUFFPAI_TOOLS_DIR": "/opt/t"}).stdout.strip() == "/opt/t"


# -- version detection -------------------------------------------------------

def _fake_python(tmp_path: Path, version: tuple[int, int, int]) -> Path:
    """A python3 that is the real one, claiming another version."""
    real = _posix(sys.executable)
    fake = tmp_path / f"python-{version[0]}.{version[1]}"
    fake.write_text(
        "#!/bin/sh\n"
        f'exec "{real}" -c "import sys; sys.version_info = {version!r}; '
        'exec(sys.argv[1])" "$2"\n', encoding="utf-8", newline="\n")
    fake.chmod(0o755)
    return fake


@needs_bash
@pytest.mark.parametrize("version,ok", [((3, 9, 6), False), ((3, 10, 12), False),
                                        ((3, 11, 0), True), ((3, 12, 15), True),
                                        ((3, 14, 2), True)])
def test_python_floor_is_3_11(tmp_path, version, ok):
    fake = _fake_python(tmp_path, version)
    got = sh(f'tools_python_ok "{_posix(fake)}" && echo YES || echo NO')
    assert got.stdout.strip() == ("YES" if ok else "NO"), got.stderr


def test_the_installers_and_setup_agree_on_3_11():
    assert 'python_requires=">=3.11"' in (ROOT / "setup.py").read_text(encoding="utf-8")
    assert ': "${PY_MIN_MINOR:=11}"' in _text()


@needs_bash
def test_a_mac_without_apple_tools_never_runs_the_stand_in_python():
    stub = ('uname() { echo Darwin; }\n'
            'command() { if [ "$1" = "-v" ]; then echo /usr/bin/python3; '
            'else builtin command "$@"; fi; }\n')
    no_tools = sh(stub + 'xcode-select() { return 2; }\n'
                  'tools_skip_python python3 && echo SKIP || echo TRY')
    assert no_tools.stdout.strip() == "SKIP"
    with_tools = sh(stub + 'xcode-select() { echo /Library/Developer/CommandLineTools; }\n'
                    'tools_skip_python python3 && echo SKIP || echo TRY')
    assert with_tools.stdout.strip() == "TRY"
    linux = sh('uname() { echo Linux; }\ntools_skip_python python3 && echo SKIP || echo TRY')
    assert linux.stdout.strip() == "TRY"


# -- an install, end to end, from a local copy --------------------------------

def _serve(tmp_path: Path, archive: Path) -> str:
    """bash that replaces the network with *archive* and pins its hash."""
    return (f'tools_fetch() {{ cp "{_posix(archive)}" "$2"; }}\n'
            f'tools_pin() {{ echo "https://nodejs.org/dist/fake {_sha(archive)}"; }}\n')


@needs_bash
def test_python_installs_into_its_own_folder_and_the_stage_is_gone(tmp_path):
    real = _posix(sys.executable)
    py = f'#!/bin/sh\nexec "{real}" "$@"\n'.encode()
    arc = _targz(tmp_path / "py.tar.gz", {"python/bin/python3": py},
                 modes={"python/bin/python3": 0o755})
    tools = tmp_path / "tools"
    got = sh(_serve(tmp_path, arc) +
             f'tools_install_python "{_posix(tools)}" linux-x64; echo "rc=$?"\n'
             'echo "py=$TOOLS_PYTHON"')
    assert "rc=0" in got.stdout, got.stdout + got.stderr
    home = tools / "python-3.12.15-20261003"
    assert f"py={_posix(home)}/bin/python3" in got.stdout
    assert (home / "bin" / "python3").is_file()
    assert [p.name for p in tools.iterdir()] == [home.name]     # no download folder left


@needs_bash
def test_a_tampered_python_is_never_installed(tmp_path):
    arc = _targz(tmp_path / "py.tar.gz", {"python/bin/python3": b"#!/bin/sh\nexit 0\n"},
                 modes={"python/bin/python3": 0o755})
    tools = tmp_path / "tools"
    script = (f'tools_fetch() {{ cp "{_posix(arc)}" "$2"; }}\n'
              f'tools_pin() {{ echo "https://nodejs.org/dist/fake {"b" * 64}"; }}\n'
              f'tools_install_python "{_posix(tools)}" linux-x64; echo "rc=$?"')
    got = sh(script)
    assert "rc=1" in got.stdout
    assert not (tools / "python-3.12.15-20261003").exists()
    assert list(tools.iterdir()) == []


@needs_bash
def test_node_installs_and_a_second_run_reuses_it(tmp_path):
    name = "node-v24.21.0-linux-x64"
    arc = _targz(tmp_path / "node.tar.gz",
                 {f"{name}/bin/node": b"#!/bin/sh\necho v24.21.0\n"},
                 modes={f"{name}/bin/node": 0o755})
    tools = tmp_path / "tools"
    first = sh(_serve(tmp_path, arc) + f'tools_install_node "{_posix(tools)}" linux-x64; '
               'echo "rc=$? home=$TOOLS_NODE_HOME"')
    assert "rc=0" in first.stdout, first.stdout + first.stderr
    assert (tools / name / "bin" / "node").is_file()
    again = sh('tools_fetch() { echo FETCHED; return 1; }\n'
               f'tools_install_node "{_posix(tools)}" linux-x64; echo "rc=$?"')
    assert "rc=0" in again.stdout and "FETCHED" not in again.stdout
    assert "already in" in again.stdout


@needs_bash
def test_ollama_is_only_for_a_mac(tmp_path):
    got = sh(f'tools_install_ollama "{_posix(tmp_path / "t")}" linux-x64; echo "rc=$?"')
    assert "rc=1" in got.stdout and not (tmp_path / "t").exists()


@needs_bash
def test_dry_run_downloads_nothing(tmp_path):
    tools = tmp_path / "tools"
    got = sh('tools_fetch() { echo FETCHED; }\n'
             f'tools_install_python "{_posix(tools)}" linux-x64\n'
             f'tools_install_node "{_posix(tools)}" darwin-arm64\n'
             f'tools_install_ollama "{_posix(tools)}" darwin-arm64\n',
             env={"DRY_RUN": "1"})
    assert "FETCHED" not in got.stdout
    assert got.stdout.count("[dry-run] would download") == 3
    assert not tools.exists()


# -- the Mac launchers -------------------------------------------------------

@needs_bash
def test_the_mac_launchers_open_the_bash_ones_from_a_folder_with_odd_characters(tmp_path):
    repo = tmp_path / "Bot's folder $HOME (1)"
    repo.mkdir()
    for target, word in (("start-bot.sh", "STARTED"), ("stop-bot.sh", "STOPPED")):
        f = repo / target
        f.write_text(f"#!/bin/sh\necho {word} in \"$(pwd)\"\n", encoding="utf-8", newline="\n")
        f.chmod(0o755)
    out = tmp_path / "out"
    out.mkdir()
    got = sh('tools_write_mac_launchers "$REPO" "$OUT"; echo "rc=$?"',
             env={"REPO": _posix(repo), "OUT": _posix(out)})
    assert "rc=0" in got.stdout, got.stderr
    for name, word in (("Start BuildBot.command", "STARTED"), ("Stop BuildBot.command", "STOPPED")):
        cmd = out / name
        body = cmd.read_text(encoding="utf-8")
        assert body.startswith("#!/bin/bash\n") and "\r" not in body
        if os.name != "nt":
            assert cmd.stat().st_mode & stat.S_IXUSR
        ran = subprocess.run([BASH, _posix(cmd)], capture_output=True, text=True, timeout=60)
        assert ran.returncode == 0, ran.stderr
        assert word in ran.stdout and "You can close this window." in ran.stdout
        assert "$HOME (1)" in ran.stdout          # the name was not expanded


@needs_bash
def test_mac_launchers_dry_run_writes_nothing(tmp_path):
    got = sh(f'tools_write_mac_launchers "{_posix(tmp_path)}" "{_posix(tmp_path)}"',
             env={"DRY_RUN": "1"})
    assert got.stdout.count("[dry-run] would write") == 2
    assert not list(tmp_path.glob("*.command"))


# -- what a person reads ------------------------------------------------------

def _printed(text: str) -> list[str]:
    return [m.group(1) for m in re.finditer(r'^\s*say "(.*)"\s*$', text, re.M)]


@needs_bash
def test_messages_print_even_where_a_say_program_exists(tmp_path):
    """A Mac has /usr/bin/say (it speaks). The helper must print, not speak."""
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "say").write_text("#!/bin/sh\necho SPOKEN\n", encoding="utf-8", newline="\n")
    (fake / "say").chmod(0o755)
    path = _posix(fake) + ":" + os.environ.get("PATH", "")
    got = subprocess.run([BASH, "-c", f'. "{_posix(LIB)}"; say "printed line"'],
                         capture_output=True, text=True, timeout=60,
                         env=dict(os.environ, PATH=path))
    assert got.stdout.strip() == "printed line"


def test_everything_the_helper_prints_is_plain():
    lines = _printed(_text())
    assert lines
    for line in lines:
        assert plain_words.is_plain(line, also=("drawbridge",)), plain_words.check(line)
        assert "beta" not in line.lower() and "—" not in line


# -- install.sh, green, as a practice run --------------------------------------

def _green_dry_run(tmp_path: Path, platform: str, extra=(), *,
                   tools: str = "") -> subprocess.CompletedProcess:
    shim = tmp_path / "shim"
    shim.mkdir()
    (shim / "python3").write_text(f'#!/bin/sh\nexec "{_posix(sys.executable)}" "$@"\n',
                                  encoding="utf-8", newline="\n")
    (shim / "python3").chmod(0o755)
    env = dict(os.environ)
    env["PATH"] = str(shim) + os.pathsep + env.get("PATH", "")
    env["PUFFPAI_INSTALL_PRETEND_MISSING"] = "python,node,ollama"
    env["PUFFPAI_TOOLS_PLATFORM"] = platform
    env["PUFFPAI_TOOLS_DIR"] = tools or _posix(tmp_path / "tools")
    env["OLLAMA_HOST"] = "http://127.0.0.1:9"
    for name in ("PUFFPAI_STATE_DIR", "PUFFPAI_DATA_DIR"):
        env.pop(name, None)
    return subprocess.run(
        [BASH, _posix(ROOT / "install.sh"), "--no-questions", "--port", "18251",
         "--dashboard-port", "13251", "--state-dir", _posix(tmp_path / "state"), *extra],
        capture_output=True, text=True, env=env, timeout=300, stdin=subprocess.DEVNULL)


@needs_bash
def test_a_green_linux_practice_run_offers_python_and_node_with_one_question(tmp_path):
    got = _green_dry_run(tmp_path, "linux-x64", ("--dry-run",))
    out = got.stdout
    assert got.returncode == 0, out + got.stderr
    assert out.count("[dry-run] would ask: Download them now?") == 1
    assert "x86_64-unknown-linux-gnu-install_only.tar.gz" in out
    assert "node-v24.21.0-linux-x64.tar.gz" in out
    assert "ollama-darwin.tgz" not in out
    assert "curl -fsSL https://ollama.com/install.sh | sh" in out     # Linux keeps its own
    assert "It does not need your password" in out
    assert not (tmp_path / "state").exists() and not (tmp_path / "tools").exists()


@needs_bash
def test_a_green_mac_practice_run_adds_ollama_and_the_double_click_files(tmp_path):
    got = _green_dry_run(tmp_path, "darwin-arm64", ("--dry-run",))
    out = got.stdout
    assert got.returncode == 0, out + got.stderr
    assert "aarch64-apple-darwin-install_only.tar.gz" in out
    assert "node-v24.21.0-darwin-arm64.tar.gz" in out
    assert "ollama-darwin.tgz" in out and "signed by Ollama" in out
    assert "Start BuildBot.command" in out and "Stop BuildBot.command" in out
    assert "right-click the file, pick Open" in out and "Open Anyway" in out
    assert "brew" not in out.lower()
    assert not (tmp_path / "state").exists() and not (tmp_path / "tools").exists()


@needs_bash
def test_with_no_yes_a_green_computer_gets_nothing_downloaded(tmp_path):
    """A real run (not practice): no one to ask, no flag, so nothing is
    downloaded and setup stops plainly before changing anything."""
    got = _green_dry_run(tmp_path, "linux-x64")
    assert got.returncode == 1
    assert "BuildBot needs Python 3.11 or newer" in got.stderr
    assert "--yes-install-tools" in got.stderr
    assert "Nothing was downloaded." in got.stdout
    assert not (tmp_path / "tools").exists() and not (tmp_path / "state").exists()


@needs_bash
def test_a_tools_folder_inside_the_install_is_refused(tmp_path):
    inside = ROOT / "tools-inside"
    got = _green_dry_run(tmp_path, "linux-x64", ("--dry-run",),
                         tools=_posix(inside))
    assert "which is not safe" in got.stdout
    assert "[dry-run] would download" not in got.stdout
    assert not inside.exists()


# -- the launchers install.sh writes -----------------------------------------

def _install_sh() -> str:
    return (ROOT / "install.sh").read_text(encoding="utf-8")


def test_own_ollama_listens_on_this_computer_only_and_stop_takes_only_it():
    sh_text = _install_sh()
    assert "OLLAMA_HOST=127.0.0.1:11434 setsid" in sh_text
    assert "OLLAMA_HOST=127.0.0.1:11434 nohup" in sh_text
    assert 'grep -q "ollama serve"' in sh_text
    assert "ollama.env" in sh_text
    assert "0.0.0.0" not in sh_text


def test_on_a_mac_every_background_start_gets_its_own_session():
    """macOS has no setsid program; without an own session stop-bot.sh's
    process-group stop would miss the children."""
    sh_text = _install_sh()
    assert sh_text.count("os.setsid()") == 2
    assert sh_text.count("-I -c 'import os, sys") == 2
