"""Guards for the root installers and the install/persona docs.

These exist because the failures they catch are silent for us and loud for a
stranger: an em dash that garbles a Windows console, an installer that drifts
off the Python floor in setup.py, and a "worked example" in the docs that does
not actually pass the validator it claims to pass.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from puffpai import app_registry

ROOT = Path(__file__).resolve().parents[1]

INSTALLERS = ("install.sh", "install.ps1")
INSTALL_DOCS = ("docs/INSTALL_LINUX.md", "docs/INSTALL_WINDOWS.md", "docs/INSTALL_MAC.md")


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


@pytest.mark.parametrize("name", INSTALLERS)
def test_installer_exists(name: str) -> None:
    assert (ROOT / name).is_file(), f"{name} is missing from the repo root."


@pytest.mark.parametrize("name", INSTALLERS + ("install.cmd",))
def test_installer_is_ascii_only(name: str) -> None:
    """The legacy Windows console garbles anything else."""
    text = _read(name)
    bad = sorted({ch for ch in text if ord(ch) > 127})
    assert not bad, (
        f"{name} contains non-ASCII characters {bad!r}. Console strings must "
        f"be ASCII: an em dash renders as mojibake on cmd.exe."
    )


def test_installers_quote_the_python_floor_from_setup_py() -> None:
    """One floor, declared once, repeated nowhere by accident."""
    match = re.search(r'python_requires=">=(\d+)\.(\d+)"', _read("setup.py"))
    assert match, "setup.py no longer declares python_requires."
    major, minor = match.group(1), match.group(2)

    sh = _read("install.sh")
    assert f"PY_MIN_MAJOR={major}" in sh and f"PY_MIN_MINOR={minor}" in sh, (
        "install.sh's Python floor has drifted from setup.py."
    )
    ps1 = _read("install.ps1")
    assert f"$PyMinMajor   = {major}" in ps1 and f"$PyMinMinor   = {minor}" in ps1, (
        "install.ps1's Python floor has drifted from setup.py."
    )


@pytest.mark.parametrize("name", INSTALLERS)
def test_installer_never_installs_ollama_or_pulls_a_model_by_default(name: str) -> None:
    """Both downloads are gated behind an explicit flag, in the text itself."""
    text = _read(name)
    assert "ollama.com/install.sh" in text or "Ollama.Ollama" in text, (
        f"{name} no longer prints official Ollama instructions."
    )
    for gate in ("install-ollama", "InstallOllama"):
        if gate.lower() in text.lower():
            break
    else:
        pytest.fail(f"{name} has no explicit opt-in flag for installing Ollama.")


@pytest.mark.parametrize("name", INSTALLERS)
def test_installer_supports_dry_run(name: str) -> None:
    assert "dry-run" in _read(name).lower(), f"{name} lost its --dry-run mode."


# --- the key, the launchers, and the ports ---------------------------------
#
# These four exist for one reason: the dashboard proxy reads PUFFPAI_API_KEY from
# its own environment and sends nothing else, so an installer that does not put
# the SAME key on both sides ships a dashboard that 401s on every call. Writing
# a key file is not enough - api_server._load_or_create_api_key prefers a stored
# secret (vault, then OS keyring) over the state-dir file, so a stale keyring
# entry from another install would win. An explicitly set PUFFPAI_API_KEY is the
# one thing that beats every store, which is why the launchers exist at all.

#: What each installer must generate, and the env file both halves read.
#: ``run-setup`` is on the list for the same reason (see tests/test_install_pointer.py): the
#: wizard has to be startable the same way the backend is, against the state dir
#: this install chose, rather than through a bare command in a bare shell.
#: The .cmd files and start/stop scripts are the ones an owner double-clicks
#: (tests/test_install_frontdoor.py); the run-*.ps1 helpers stay because the
#: wizard's closing screen names them (cli.installed_launchers).
LAUNCHERS = {
    "install.ps1": ("run-setup.ps1", "run-backend.ps1", "run-dashboard.ps1",
                    "run-start.ps1", "run-stop.ps1", "Start BuildBot.cmd",
                    "Stop BuildBot.cmd", "Set up BuildBot.cmd", "puffpai.cmd",
                    "Remove BuildBot.cmd", "remove-buildbot.ps1"),
    "install.sh": ("run-setup.sh", "run-backend.sh", "run-dashboard.sh",
                   "start-bot.sh", "stop-bot.sh"),
}


@pytest.mark.parametrize("name", INSTALLERS)
def test_installer_writes_an_env_file_and_two_launchers(name: str) -> None:
    text = _read(name)
    assert "buildbot.env" in text, (
        f"{name} no longer mints this install's key into <state>/buildbot.env."
    )
    for launcher in LAUNCHERS[name]:
        assert launcher in text, f"{name} no longer generates {launcher}."
    assert "PUFFPAI_BACKEND_URL" in text and "PUFFPAI_API_KEY" in text, (
        f"{name} must write both halves of the pair the proxy reads."
    )


@pytest.mark.parametrize("name", INSTALLERS)
def test_installer_records_whether_a_dashboard_was_asked_for(name: str) -> None:
    """A dashboard port is written either way, so it cannot answer the question.

    ``PUFFPAI_WITH_DASHBOARD`` is the line the closing screen reads to say "the
    dashboard was not installed" instead of pointing at an address with nothing
    behind it.
    """
    text = _read(name)
    assert "PUFFPAI_WITH_DASHBOARD" in text, (
        f"{name} no longer records whether the dashboard was installed."
    )


@pytest.mark.parametrize("name", INSTALLERS)
def test_installer_gives_the_dashboard_the_key_it_reads(name: str) -> None:
    """dashboard/.env.local, or the dashboard 401s and nobody knows why."""
    text = _read(name).replace("\\", "/")
    assert "dashboard/.env.local" in text, (
        f"{name} does not write dashboard/.env.local, so the proxy has no key."
    )


@pytest.mark.parametrize("name", INSTALLERS)
def test_installer_takes_ports_and_a_state_dir(name: str) -> None:
    text = _read(name)
    flags = (
        ("-Port", "-DashboardPort", "-StateDir")
        if name.endswith(".ps1")
        else ("--port", "--dashboard-port", "--state-dir")
    )
    for flag in flags:
        assert flag in text, f"{name} has no {flag} option."


@pytest.mark.parametrize("name", INSTALLERS)
def test_installer_never_prints_the_key(name: str) -> None:
    """A key echoed to a console ends up in a screenshot or a scrollback."""
    text = _read(name)
    assert "not printed" in text, (
        f"{name} no longer says the key is withheld; check that no branch "
        f"prints it."
    )
    for line in text.splitlines():
        bare = line.strip()
        if bare.startswith("#") or not bare:
            continue
        printer = any(
            token in bare
            for token in ("Write-Host", "say ", "echo ", "printf", "Say ")
        )
        names = ("$Key", '"$KEY"', "${KEY}")
        if printer and any(n in bare for n in names):
            pytest.fail(
                f"{name} looks like it prints the minted key: {bare!r}"
            )


@pytest.mark.parametrize("name", INSTALLERS)
def test_installer_drops_the_pointer_a_bare_command_reads(name: str) -> None:
    """The other half of the launchers: `puffpai setup` with no env loaded.

    Without this file the wizard resolves the DEFAULT per-user state dir while
    run-backend serves the one the installer chose - two installs, one command
    apart. See puffpai.install_pointer and tests/test_install_pointer.py.
    """
    from puffpai import install_pointer

    text = _read(name)
    assert install_pointer.POINTER_NAME in text, (
        f"{name} no longer writes {install_pointer.POINTER_NAME}, so a bare "
        f"`puffpai` command cannot find this install's state dir."
    )
    assert "env_file" in text, (
        f"{name} writes a pointer with no env_file key; install_pointer "
        f"reads that key and nothing else."
    )


@pytest.mark.parametrize("name", INSTALLERS)
def test_the_closing_text_names_the_setup_launcher_and_where(name: str) -> None:
    """What the installer says to run next has to be what it just wrote."""
    text = _read(name)
    launchers = (("Set up BuildBot.cmd", '"Start "') if name.endswith(".ps1")
                 else ("run-setup.sh", "start-bot.sh"))
    tail = text[text.index("what to do next"):]
    for launcher in launchers:
        assert launcher in tail, (
            f"{name}'s closing text does not name {launcher}."
        )
    where = "puffpai.cmd where" if name.endswith(".ps1") else "puffpai where"
    assert where in tail, (
        f"{name}'s closing text does not tell an owner how to see which "
        f"install a command will touch (`puffpai where`)."
    )


@pytest.mark.parametrize("rel", INSTALL_DOCS + ("docs/BUILD_A_PERSONA.md",))
def test_install_doc_exists_and_stays_short(rel: str) -> None:
    lines = _read(rel).splitlines()
    assert lines, f"{rel} is empty."
    # BUILD_A_PERSONA.md is the one reference page here rather than a quickstart,
    # and it grew by two sections it has to carry: `puffpai persona new` (the
    # scaffold verbs and where an owner's own apps live) and the list grammar
    # chat answers with no model. The cap still exists - it is a page, not a
    # manual - and the number moves only with content that would otherwise be
    # missing. It is 206 now, for four shipped things an owner cannot discover
    # any other way: `puffpai persona edit` / PATCH (and why there is no
    # remove_lists), the scaffold offer being unconditional, the build-context
    # block - what of the owner's own words reaches the model, quoted, and what
    # the caps on it are - and the id: where it comes from, which words are
    # refused, and that --rename-id is how it changes.
    cap = 206 if rel.endswith("BUILD_A_PERSONA.md") else 90
    assert len(lines) <= cap, (
        f"{rel} is {len(lines)} lines; the cap is {cap}. These pages earn "
        f"their keep by being short enough to read before installing."
    )


def test_getting_started_points_at_the_per_os_pages() -> None:
    text = _read("docs/GETTING_STARTED.md")
    for rel in ("INSTALL_LINUX.md", "INSTALL_WINDOWS.md", "INSTALL_MAC.md"):
        assert rel in text, f"GETTING_STARTED.md no longer links {rel}."


def _json_blocks(rel: str) -> list[dict]:
    blocks = re.findall(r"```json\n(.*?)```", _read(rel), re.S)
    return [json.loads(b) for b in blocks]


def test_persona_doc_app_example_passes_the_real_validator() -> None:
    app = _json_blocks("docs/BUILD_A_PERSONA.md")[0]
    problem = app_registry.validate_manifest(app)
    assert problem is None, (
        f"The worked app example in docs/BUILD_A_PERSONA.md does not "
        f"validate: {problem}"
    )


def test_persona_doc_pack_example_passes_the_real_validator() -> None:
    blocks = _json_blocks("docs/BUILD_A_PERSONA.md")
    app, pack = blocks[0], blocks[1]
    problem = app_registry.validate_pack(pack, installed={app["id"]})
    assert problem is None, (
        f"The worked pack example in docs/BUILD_A_PERSONA.md does not "
        f"validate: {problem}"
    )


def test_persona_doc_surface_list_matches_the_live_taxonomy() -> None:
    """A doc that lists surfaces the build does not have sends people wrong."""
    from puffpai import build_interview

    text = _read("docs/BUILD_A_PERSONA.md")
    for surface in build_interview.surface_taxonomy():
        assert surface in text, (
            f"docs/BUILD_A_PERSONA.md omits the surface {surface!r}; the "
            f"list is meant to be the whole taxonomy."
        )
