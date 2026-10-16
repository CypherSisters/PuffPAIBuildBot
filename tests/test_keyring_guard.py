"""No test ever writes to, or deletes from, the real OS password store.

Test runs kept leaving real Credential Manager entries named
PUFFPAI_API_KEY.state.<id>. tests/conftest.py now gives every test a fresh
stand-in keyring, points every process the run starts at keyring's "fail"
backend, traps the Windows calls that write or delete a credential, and fails
any test that reached the real store. A test about keyring behaviour opts in
with ``@pytest.mark.keyring_stub``.

And ``puffpai serve`` / ``puffpai start`` say, in one plain line, when they
make a new key entry for a build: it is never made silently.
"""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

import conftest
from puffpai.security import api_key_store, secrets_provider


def test_every_test_gets_a_stand_in_that_refuses_writes():
    import keyring
    assert getattr(keyring, conftest._STUB_MARK, False)          # noqa: SLF001
    assert secrets_provider.keyring is keyring
    assert keyring.get_password("puffpai", "PUFFPAI_API_KEY") is None
    with pytest.raises(RuntimeError, match="keyring_stub"):
        keyring.set_password("puffpai", "anything", "x")
    with pytest.raises(RuntimeError, match="keyring_stub"):
        keyring.delete_password("puffpai", "anything")


def test_the_stand_in_is_fresh_for_every_test():
    import keyring
    first = keyring
    assert conftest.REAL_KEYRING_TOUCHES == []
    assert first is sys.modules["keyring"]


@pytest.mark.keyring_stub
def test_a_marked_test_gets_one_that_keeps_writes(keyring_entries):
    import keyring
    keyring.set_password("puffpai", "an-entry", "a-value")
    assert keyring_entries == {("puffpai", "an-entry"): "a-value"}
    keyring.delete_password("puffpai", "an-entry")
    assert keyring_entries == {}


def test_every_process_the_run_starts_uses_the_fail_backend():
    assert os.environ["PYTHON_KEYRING_BACKEND"] == "keyring.backends.fail.Keyring"
    child = (
        "import keyring\n"
        "backend = keyring.get_keyring()\n"
        "module = type(backend).__module__\n"
        "print(module)\n"
        "assert module == 'keyring.backends.fail', module\n"
        "try:\n"
        "    backend.set_password('puffpai', 'PUFFPAI_API_KEY.test.guard', 'x')\n"
        "except Exception as exc:\n"
        "    print('refused', type(exc).__name__)\n")
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    done = subprocess.run([sys.executable, "-c", child], capture_output=True,
                          text=True, env=env, timeout=120, check=False)
    if "No module named 'keyring'" in done.stderr:
        pytest.skip("the keyring package is not installed")
    assert done.returncode == 0, done.stderr
    assert done.stdout.splitlines() == ["keyring.backends.fail", "refused NoKeyringError"]


def test_a_call_that_reaches_the_real_store_is_trapped_and_recorded():
    try:
        from win32ctypes.pywin32 import win32cred
    except ImportError:
        pytest.skip("no Windows credential calls on this computer")
    before = len(conftest.REAL_KEYRING_TOUCHES)
    try:
        with pytest.raises(RuntimeError, match="real OS password store"):
            win32cred.CredWrite({"TargetName": "PUFFPAI_API_KEY.state.guard@puffpai",
                                 "Type": 1}, 0)
        with pytest.raises(RuntimeError, match="real OS password store"):
            win32cred.CredDelete(Type=1, TargetName="PUFFPAI_API_KEY.state.guard@puffpai")
        touched = conftest.REAL_KEYRING_TOUCHES[before:]
        assert len(touched) == 2
        assert all("PUFFPAI_API_KEY.state.guard" in row for row in touched)
    finally:
        # This test caused them on purpose; the guard would fail it otherwise.
        del conftest.REAL_KEYRING_TOUCHES[before:]


# ── a new key entry is never made silently ──────────────────────────────────

def _fresh(monkeypatch, tmp_path):
    from puffpai import api_server
    state = tmp_path / "bot-state"
    state.mkdir()
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(state))
    monkeypatch.delenv("PUFFPAI_API_KEY", raising=False)
    monkeypatch.setattr(api_server, "_LEGACY_API_KEY_PATH",
                        str(state / "absent" / "api_key.txt"))
    monkeypatch.setattr(secrets_provider, "_HAS_KEYRING", True)
    monkeypatch.setattr(secrets_provider, "_default_provider", None)
    return str(state)


@pytest.mark.keyring_stub
@pytest.mark.parametrize("how", ["serve", "start"])
def test_making_a_new_key_entry_prints_one_plain_line(how, keyring_entries,
                                                      monkeypatch, tmp_path, capsys):
    from puffpai import api_server, plain_words, start_cli
    state = _fresh(monkeypatch, tmp_path)
    make = (api_server._load_or_create_api_key if how == "serve"
            else start_cli._api_key)
    key = make()
    entry = api_key_store.entry_name(state)
    assert keyring_entries == {("puffpai", entry): key}
    err = capsys.readouterr().err
    made = [line for line in err.splitlines() if "made a new one" in line]
    assert len(made) == 1, err
    assert entry in made[0] and key not in err
    assert not plain_words.check(made[0].replace("[Security] ", ""))
    # The next start finds it and says so; it does not make another.
    assert make() == key
    err = capsys.readouterr().err
    assert "made a new one" not in err and "own entry" in err
    assert len(keyring_entries) == 1


def test_without_the_marker_no_key_entry_is_made(monkeypatch, tmp_path, capsys):
    """An unmarked test that mints a key gets the key file, not an entry."""
    from puffpai import api_server
    state = _fresh(monkeypatch, tmp_path)
    key = api_server._load_or_create_api_key()
    assert open(os.path.join(state, "api_key.txt"), encoding="utf-8").read().strip() == key
    err = capsys.readouterr().err
    assert "made a new one" in err and key not in err
