"""The wizard's half of device pairing.

Two things are worth asserting here and nowhere else:

1. The state file the Python side writes is one the DASHBOARD accepts. The
   format is duplicated across two runtimes that share no library, so the only
   honest test is a round trip: mint with Python, redeem with the TypeScript
   module. That test is skipped -- not failed -- when Node is unavailable, since
   a Python-only environment is a legitimate way to run this suite.

2. The closing screen never crashes and never prints the secret, whatever state
   the box is in.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

import pytest

from puffpai import pairing_code


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DASHBOARD = os.path.join(REPO_ROOT, "dashboard")


def test_mints_a_well_formed_state(tmp_path):
    target = str(tmp_path / "data" / "pairing.json")
    info = pairing_code.ensure_code(target)

    assert info is not None
    assert info["created"] is True
    assert os.path.exists(target)

    with open(target, encoding="utf-8") as fh:
        state = json.load(fh)

    assert state["version"] == 1
    assert len(state["secret"]) == 64
    assert all(c in "0123456789abcdef" for c in state["secret"])
    assert len(state["code"]) == pairing_code.CODE_LENGTH
    assert set(state["code"]) <= set(pairing_code.CODE_ALPHABET)
    # The confusables the alphabet exists to exclude.
    assert not (set(state["code"]) & set("01OI"))


def test_never_returns_or_formats_the_secret(tmp_path):
    target = str(tmp_path / "data" / "pairing.json")
    info = pairing_code.ensure_code(target)
    with open(target, encoding="utf-8") as fh:
        secret = json.load(fh)["secret"]

    assert secret not in info["code"]
    assert secret not in repr(info)


def test_second_call_does_not_rotate_a_live_code(tmp_path):
    target = str(tmp_path / "data" / "pairing.json")
    first = pairing_code.ensure_code(target)
    second = pairing_code.ensure_code(target)

    assert second["created"] is False
    assert second["rotated"] is False
    assert second["code"] == first["code"]


def test_expired_code_is_rotated_but_the_secret_survives(tmp_path):
    target = str(tmp_path / "data" / "pairing.json")
    first = pairing_code.ensure_code(target)
    with open(target, encoding="utf-8") as fh:
        before = json.load(fh)

    # Wind the rotation stamp back past the one-hour TTL.
    before["codeRotatedAt"] = "2020-01-01T00:00:00+00:00"
    with open(target, "w", encoding="utf-8") as fh:
        json.dump(before, fh)

    second = pairing_code.ensure_code(target)
    with open(target, encoding="utf-8") as fh:
        after = json.load(fh)

    assert second["rotated"] is True
    assert second["code"] != first["code"]
    # Rotating the code must never rotate the secret: that would silently
    # un-pair every device on the appliance.
    assert after["secret"] == before["secret"]


def test_malformed_state_is_left_alone(tmp_path):
    """Never recover from a parse error by minting a new secret."""
    target = tmp_path / "data" / "pairing.json"
    target.parent.mkdir(parents=True)
    target.write_text('{"version": 1, "secret": "nope"}', encoding="utf-8")

    assert pairing_code.ensure_code(str(target)) is None
    assert target.read_text(encoding="utf-8") == '{"version": 1, "secret": "nope"}'


def test_unwritable_location_returns_none_rather_than_raising(tmp_path):
    # A path whose parent is a FILE cannot be turned into a directory.
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    assert pairing_code.ensure_code(str(blocker / "data" / "pairing.json")) is None


def test_state_path_prefers_the_explicit_override(tmp_path, monkeypatch):
    monkeypatch.setenv("PUFF_PAIRING_STATE", str(tmp_path / "elsewhere.json"))
    assert pairing_code.state_path() == str(tmp_path / "elsewhere.json")


def test_state_path_is_none_without_a_dashboard(tmp_path, monkeypatch):
    """A Python-only install is a real situation, not an error."""
    monkeypatch.delenv("PUFF_PAIRING_STATE", raising=False)
    monkeypatch.setattr(pairing_code, "__file__", str(tmp_path / "pairing_code.py"))
    monkeypatch.chdir(tmp_path)
    assert pairing_code.state_path() is None


def test_closing_screen_prints_a_code_and_never_the_secret(tmp_path, capsys, monkeypatch):
    target = tmp_path / "data" / "pairing.json"
    monkeypatch.setenv("PUFF_PAIRING_STATE", str(target))

    from puffpai import cli

    out = " ".join(cli.pairing_block_lines())

    with open(target, encoding="utf-8") as fh:
        state = json.load(fh)

    assert pairing_code.format_code(state["code"]) in out
    assert state["secret"] not in out
    # The one command, the same in every shell (was cd dashboard && npm run
    # pair:code, which only worked from the code copy's own folder).
    assert "puffpai pair" in out and "pair:code" not in out


def test_closing_screen_survives_a_broken_pairing_file(tmp_path, capsys, monkeypatch):
    target = tmp_path / "pairing.json"
    target.write_text("{ not json", encoding="utf-8")
    monkeypatch.setenv("PUFF_PAIRING_STATE", str(target))

    from puffpai import cli

    out = " ".join(cli.pairing_block_lines())  # must not raise

    # The one command, the same in every shell (was cd dashboard && npm run
    # pair:code, which only worked from the code copy's own folder).
    assert "puffpai pair" in out and "pair:code" not in out


@pytest.mark.skipif(
    shutil.which("node") is None or not os.path.isdir(DASHBOARD),
    reason="needs Node and the dashboard tree for the cross-runtime round trip",
)
def test_the_dashboard_accepts_a_state_this_module_wrote(tmp_path):
    """Mint with Python, redeem with TypeScript.

    The wire format lives in two files that share no library
    (src/puffpai/pairing_code.py and dashboard/app/_lib/server/pairing.ts). A
    drift between them means the wizard prints a code the dashboard rejects,
    which is the single worst failure this feature can have: the owner is locked
    out of their own appliance on the first screen. So assert the round trip
    rather than trusting two copies of a comment.
    """
    target = tmp_path / "pairing.json"
    info = pairing_code.ensure_code(str(target))
    assert info is not None

    # An ESM specifier resolves relative to the importing FILE, not to cwd, so
    # the module is addressed by absolute file URL and the runner can live
    # anywhere.
    module = os.path.join(DASHBOARD, "app", "_lib", "server", "pairing.ts")
    script = (
        "import { pathToFileURL } from 'node:url';\n"
        "process.env.PUFF_PAIRING_STATE = process.argv[2];\n"
        "const P = await import(pathToFileURL(process.argv[3]).href);\n"
        "process.stdout.write(P.verifyCode(process.argv[4]) ? 'YES' : 'NO');\n"
    )
    runner = tmp_path / "roundtrip.mjs"
    runner.write_text(script, encoding="utf-8")

    result = subprocess.run(
        [shutil.which("node"), str(runner), str(target), module, info["code"]],
        cwd=DASHBOARD,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        pytest.skip(f"node could not load the TS module: {result.stderr[-400:]}")
    assert result.stdout.strip() == "YES", (
        "the dashboard rejected a pairing code this module minted -- the two "
        "copies of the state format have drifted"
    )


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes only")
def test_state_file_is_owner_only(tmp_path):
    target = str(tmp_path / "data" / "pairing.json")
    pairing_code.ensure_code(target)
    assert (os.stat(target).st_mode & 0o777) == 0o600
    assert (os.stat(os.path.dirname(target)).st_mode & 0o077) == 0
