"""The unregister guard: BuildBot can only ever stop or remove its OWN locked
box, never one of her distros, and never runs ``wsl --shutdown``.
"""

from __future__ import annotations

import json
import os

import pytest

from puffpai.box import driver
from puffpai.box import wording as W

from test_box_status_states import FakeWsl, _claim, _home, box_env  # noqa: F401  (fixture)

#: The WSL box is Windows only: off Windows box.driver answers before it
#: ever reaches WSL (Linux uses bubblewrap), so these have nothing to test.
wsl_box_only = pytest.mark.skipif(os.name != "nt", reason="the WSL box is Windows only")


def _no_shutdown(fake: FakeWsl) -> None:
    for call in fake.calls:
        assert not any(w.lower() in ("--shutdown", "-shutdown") for w in call), call


def _removed(fake: FakeWsl) -> list[str]:
    return [c[2] for c in fake.calls if len(c) == 3 and c[1] in ("--terminate", "--unregister")]


@wsl_box_only
def test_box_json_naming_ubuntu_is_refused(box_env, monkeypatch):
    """box.json is a hint: even a box.json that names Ubuntu, with Ubuntu's
    disk (in this fake) sitting in the box root, cannot get Ubuntu removed."""
    fake = FakeWsl(monkeypatch)
    fake.add("Ubuntu", _home(box_env, "Ubuntu"), marker={"distro": "Ubuntu"})
    _claim(box_env, "PuffPAI-sandbox")
    hint = driver._box_json_path()
    os.makedirs(os.path.dirname(hint), exist_ok=True)
    with open(hint, "w", encoding="utf-8") as fh:
        json.dump({"distro": "Ubuntu", "wsl_version_sha": "0" * 64}, fh)
    assert driver.guard("Ubuntu") == (False, "name")
    got = driver.remove_box()
    assert got["removed"] is False
    monkeypatch.setenv("PUFFPAI_BOX_DISTRO", "Ubuntu")
    got = driver.remove_box()
    assert got == {"removed": False, "why": "name"}
    assert "Ubuntu" not in _removed(fake)
    assert any(e["name"] == "Ubuntu" for e in fake.registry)
    _no_shutdown(fake)


@pytest.mark.parametrize("name", ["Ubuntu", "jetson-flash", "docker-desktop",
                                  "UBUNTU", "PuffPAI-sandbox;rm", "PuffPAI-sandbox/..",
                                  "puffpai-sandbox-x ", ""])
def test_guard_refuses_names_that_are_not_ours(box_env, monkeypatch, name):
    FakeWsl(monkeypatch)
    ok, _why = driver.guard(name)
    assert ok is False


def test_puffpai_sandbox_with_a_base_path_outside_the_root_is_refused(box_env, monkeypatch):
    fake = FakeWsl(monkeypatch)
    outside = box_env["tmp"] / "somewhere-else" / "PuffPAI-sandbox"
    outside.mkdir(parents=True)
    fake.add("PuffPAI-sandbox", str(outside))
    _claim(box_env, "PuffPAI-sandbox")           # even with our claim
    monkeypatch.setenv("PUFFPAI_BOX_DISTRO", "PuffPAI-sandbox")
    assert driver.guard("PuffPAI-sandbox") == (False, "base_path")
    got = driver.remove_box()
    assert got["removed"] is False
    assert _removed(fake) == []
    assert any(e["name"] == "PuffPAI-sandbox" for e in fake.registry)
    _no_shutdown(fake)


def test_a_unc_or_device_base_path_is_never_ours(box_env, monkeypatch):
    FakeWsl(monkeypatch)
    root = str(box_env["root"] / "PuffPAI-sandbox")
    assert driver.base_path_is_ours("PuffPAI-sandbox", root)
    assert driver.base_path_is_ours("PuffPAI-sandbox", "\\\\?\\" + root)
    assert not driver.base_path_is_ours("PuffPAI-sandbox", "\\\\?\\UNC\\srv\\share\\x")
    assert not driver.base_path_is_ours("PuffPAI-sandbox", "\\\\srv\\share\\PuffPAI-sandbox")
    assert not driver.base_path_is_ours("PuffPAI-sandbox", root + "-2")
    assert not driver.base_path_is_ours("PuffPAI-sandbox", str(box_env["root"]))


def test_without_our_marker_the_guard_refuses(box_env, monkeypatch):
    fake = FakeWsl(monkeypatch)
    fake.add("PuffPAI-sandbox", _home(box_env, "PuffPAI-sandbox"), marker=False)
    _claim(box_env, "PuffPAI-sandbox")
    assert driver.guard("PuffPAI-sandbox") == (False, "marker")
    assert driver.remove_box()["removed"] is False
    assert _removed(fake) == []


def test_two_registry_entries_with_one_name_are_refused(box_env, monkeypatch):
    fake = FakeWsl(monkeypatch)
    home = _home(box_env, "PuffPAI-sandbox")
    fake.add("PuffPAI-sandbox", home)
    fake.add("PuffPAI-sandbox", home)
    assert driver.guard("PuffPAI-sandbox") == (False, "not_registered")


@wsl_box_only
def test_our_box_is_terminated_then_unregistered_and_tidied(box_env, monkeypatch):
    fake = FakeWsl(monkeypatch)
    fake.add("Ubuntu", r"C:\Users\x\AppData\Local\Packages\Ubuntu\LocalState")
    fake.add("PuffPAI-sandbox", _home(box_env, "PuffPAI-sandbox"))
    claim = _claim(box_env, "PuffPAI-sandbox")
    got = driver.remove_box()
    assert got == {"removed": True, "why": ""}
    assert _removed(fake) == ["PuffPAI-sandbox", "PuffPAI-sandbox"]
    assert [c[1] for c in fake.calls if c[1] in ("--terminate", "--unregister")] == [
        "--terminate", "--unregister"]
    assert not claim.exists()
    assert not (box_env["root"] / "PuffPAI-sandbox").exists()
    assert [e["name"] for e in fake.registry] == ["Ubuntu"]
    assert not fake.ran("-d", "Ubuntu")
    _no_shutdown(fake)


@wsl_box_only
def test_another_live_claim_keeps_the_box(box_env, monkeypatch):
    fake = FakeWsl(monkeypatch)
    fake.add("PuffPAI-sandbox", _home(box_env, "PuffPAI-sandbox"))
    mine = _claim(box_env, "PuffPAI-sandbox")
    other = box_env["tmp"] / "other-install"
    other.mkdir()
    _claim(box_env, "PuffPAI-sandbox", install_root=other, cid="c" * 16)
    got = driver.remove_box()
    assert got == {"removed": False, "why": W.SHARED_STAYS}
    assert not mine.exists()                     # our claim dropped
    assert _removed(fake) == []                  # the box stays for the other one


@wsl_box_only
def test_dead_claims_do_not_keep_the_box(box_env, monkeypatch):
    fake = FakeWsl(monkeypatch)
    fake.add("PuffPAI-sandbox", _home(box_env, "PuffPAI-sandbox"))
    _claim(box_env, "PuffPAI-sandbox")
    _claim(box_env, "PuffPAI-sandbox", install_root=box_env["tmp"] / "deleted", cid="d" * 16)
    assert driver.remove_box()["removed"] is True


@wsl_box_only
def test_an_unreadable_claim_counts_as_live(box_env, monkeypatch):
    fake = FakeWsl(monkeypatch)
    fake.add("PuffPAI-sandbox", _home(box_env, "PuffPAI-sandbox"))
    _claim(box_env, "PuffPAI-sandbox")
    bad = box_env["root"] / "claims" / "PuffPAI-sandbox" / ("e" * 16 + ".json")
    bad.write_text("{not json", encoding="utf-8")
    assert driver.remove_box()["why"] == W.SHARED_STAYS
    assert _removed(fake) == []


@wsl_box_only
def test_a_box_this_install_never_claimed_is_not_removed(box_env, monkeypatch):
    fake = FakeWsl(monkeypatch)
    monkeypatch.setenv("PUFFPAI_BOX_DISTRO", "PuffPAI-sandbox")
    fake.add("PuffPAI-sandbox", _home(box_env, "PuffPAI-sandbox"))
    assert driver.remove_box() == {"removed": False, "why": "not_claimed"}
    assert _removed(fake) == []


@wsl_box_only
def test_orphan_removal_takes_only_orphans(box_env, monkeypatch):
    fake = FakeWsl(monkeypatch)
    fake.add("PuffPAI-sandbox", _home(box_env, "PuffPAI-sandbox"))       # orphan
    fake.add("PuffPAI-sandbox-live", _home(box_env, "PuffPAI-sandbox-live"))
    live = box_env["tmp"] / "live-install"
    live.mkdir()
    _claim(box_env, "PuffPAI-sandbox-live", install_root=live, cid="f" * 16)
    fake.add("PuffPAI-sandbox-alien", str(box_env["tmp"] / "alien"))    # not in our root
    got = driver.remove_box(orphan=True)
    assert got["removed"] is True
    assert _removed(fake) == ["PuffPAI-sandbox", "PuffPAI-sandbox"]
    names = {e["name"] for e in fake.registry}
    assert names == {"PuffPAI-sandbox-live", "PuffPAI-sandbox-alien"}
    _no_shutdown(fake)


def test_wsl_refuses_shutdown_and_unguarded_removal(monkeypatch):
    calls = []
    monkeypatch.setattr(driver, "wsl_exe", lambda: r"C:\Windows\System32\wsl.exe")
    monkeypatch.setattr(driver, "_spawn", lambda argv, **k: calls.append(argv))
    for args in (["--shutdown"], ["--SHUTDOWN"], ["-l", "--shutdown"]):
        with pytest.raises(driver.GuardRefused):
            driver._wsl(args)
    with pytest.raises(driver.GuardRefused):
        driver._wsl(["--unregister", "PuffPAI-sandbox"])          # not through the guard
    with pytest.raises(driver.GuardRefused):
        driver._wsl(["--terminate", "PuffPAI-sandbox"])
    for bad in (["--terminate", "Ubuntu"], ["--unregister", "docker-desktop"],
                ["--unregister", "PuffPAI-sandbox", "extra"], ["-t", "jetson-flash"],
                # review finding: "$" accepted a trailing newline
                ["--unregister", "PuffPAI-sandbox-dev\n"], ["--terminate", "PuffPAI-sandbox\n"],
                ["--unregister", "PuffPAI-sandbox-dev\r\n"]):
        with pytest.raises(driver.GuardRefused):
            driver._wsl(bad, _guarded=True)
    assert calls == []
    assert not driver.NAME_RE.match("PuffPAI-sandbox-dev\n")
    assert driver.guard("PuffPAI-sandbox-dev\n") == (False, "name")


def test_wsl_is_only_ever_run_by_absolute_path(monkeypatch):
    monkeypatch.setattr(driver, "_optional", lambda name: None)
    monkeypatch.setenv("SystemRoot", r"C:\NoSuchWindows")
    assert driver.wsl_exe() is None
    monkeypatch.delenv("SystemRoot")
    monkeypatch.delenv("SYSTEMROOT", raising=False)
    assert driver.wsl_exe() is None
    src = open(driver.__file__, encoding="utf-8").read()
    assert "import shutil" not in src and "which(" not in src
    assert '"wsl"' not in src.replace('hp.path("wsl")', "")
