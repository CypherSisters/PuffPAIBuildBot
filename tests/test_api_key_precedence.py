"""An explicitly set PUFFPAI_API_KEY outranks every stored copy.

The bug this file locks down: `puffpai serve` was started with
PUFFPAI_API_KEY=<64 hex> in its environment, but the backend authorised a STALE
keyring entry (`puffpai` / `PUFFPAI_API_KEY`) left behind by an unrelated
install and answered 401 to the very key the dashboard proxy had been given.
The operator's choice must win, `puffpai apikey` must print the same key the
server accepts, and a junk env value must fail closed instead of silently
falling back to a stored key nobody asked for.

Run:
    pytest tests/test_api_key_precedence.py
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from puffpai import api_server
from puffpai.security import secrets_provider

#: These tests are about keyring behaviour: each gets an in-memory stand-in
#: (tests/conftest.py) and may put its own in place. Never the real store.
pytestmark = pytest.mark.keyring_stub

# Neither of these is a credential for anything: 64 hex chars each, shaped like
# a real key so validate_api_key accepts them, and distinct so the test can say
# which store won.
ENV_KEY = "e0" * 32
STORED_KEY = "5d" * 32


@pytest.fixture
def stored_key(monkeypatch):
    """Put a value in the (stubbed) OS keyring and refuse any write to it.

    conftest already installs an inert `keyring` module; this narrows it per
    test so nothing here can reach the real Credential Manager.
    """
    def _install(value: str | None) -> dict[str, int]:
        calls = {"writes": 0}

        def _get_password(service, name):
            # Keys are per build now: this build's own entry name.
            from puffpai.security import api_key_store
            if (service, name) == ("puffpai", api_key_store.entry_name()):
                return value
            return None

        def _set_password(service, name, secret):
            calls["writes"] += 1
            raise AssertionError(
                "the resolver must not write a key to the OS keyring here "
                f"(service={service!r}, entry={name!r})")

        monkeypatch.setattr(secrets_provider.keyring, "get_password",
                            _get_password, raising=False)
        monkeypatch.setattr(secrets_provider.keyring, "set_password",
                            _set_password, raising=False)
        return calls

    return _install


@pytest.fixture(autouse=True)
def _isolated_state_dir(monkeypatch, tmp_path):
    """No test here may read or write the developer's real key file."""
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(api_server, "_LEGACY_API_KEY_PATH",
                        str(tmp_path / "absent" / "api_key.txt"))


# ── 1. the environment beats the keyring ────────────────────────────────────

def test_an_explicit_env_key_beats_a_stored_key(monkeypatch, stored_key):
    stored_key(STORED_KEY)
    monkeypatch.setenv("PUFFPAI_API_KEY", ENV_KEY)
    assert api_server._load_or_create_api_key() == ENV_KEY


def test_the_env_key_is_stripped_and_its_source_logged_without_the_value(
        monkeypatch, stored_key, capsys):
    stored_key(STORED_KEY)
    monkeypatch.setenv("PUFFPAI_API_KEY", f"  {ENV_KEY}\n")
    assert api_server._load_or_create_api_key() == ENV_KEY
    captured = capsys.readouterr()
    assert "API key source: environment" in captured.err
    assert ENV_KEY not in captured.err and ENV_KEY not in captured.out


def test_no_key_is_minted_or_stored_while_an_env_key_is_present(
        monkeypatch, stored_key, tmp_path):
    """Empty stores + an env key must still not create anything."""
    calls = stored_key(None)
    monkeypatch.setenv("PUFFPAI_API_KEY", ENV_KEY)
    assert api_server._load_or_create_api_key() == ENV_KEY
    assert calls["writes"] == 0
    assert not Path(api_server._api_key_path()).exists()


# ── 2. `puffpai apikey` prints what the server accepts ──────────────────────

def test_puffpai_apikey_prints_the_env_key(monkeypatch, stored_key, capsys):
    from puffpai import cli

    stored_key(STORED_KEY)
    monkeypatch.setenv("PUFFPAI_API_KEY", ENV_KEY)
    assert cli._cmd_apikey(argparse.Namespace()) == 0
    captured = capsys.readouterr()
    # stdout is only the key: the documented workflow pastes it into
    # dashboard/.env.local, so the source note lives on stderr.
    assert captured.out.strip() == ENV_KEY
    assert STORED_KEY not in captured.out
    assert ENV_KEY not in captured.err


# ── 3. a junk env key fails closed ──────────────────────────────────────────

@pytest.mark.parametrize("junk", ["CHANGE_ME", "changeme", "placeholder",
                                  "paste-your-backend-api-key-here", "short"])
def test_a_placeholder_env_key_is_refused_not_replaced(
        monkeypatch, stored_key, junk):
    stored_key(STORED_KEY)
    monkeypatch.setenv("PUFFPAI_API_KEY", junk)
    with pytest.raises(RuntimeError) as err:
        api_server._load_or_create_api_key()
    message = str(err.value)
    assert "refusing to start" in message
    assert "PUFFPAI_API_KEY environment variable" in message
    # The bad value is never echoed back, and the stored key is NOT substituted.
    assert f"'{junk}'" not in message and f'"{junk}"' not in message
    assert STORED_KEY not in message


# ── 4. with no env key, the stored key is used unchanged ────────────────────

def test_without_an_env_key_the_stored_key_wins(monkeypatch, stored_key):
    stored_key(STORED_KEY)
    monkeypatch.delenv("PUFFPAI_API_KEY", raising=False)
    assert api_server._load_or_create_api_key() == STORED_KEY


@pytest.mark.parametrize("blank", ["", "   ", "\n"])
def test_a_blank_env_key_means_not_chosen_and_falls_through(
        monkeypatch, stored_key, blank):
    """Blank is "unset", not "refuse": there is nothing to honour or reject."""
    stored_key(STORED_KEY)
    monkeypatch.setenv("PUFFPAI_API_KEY", blank)
    assert api_server._load_or_create_api_key() == STORED_KEY


def test_the_key_file_is_used_only_when_no_env_and_no_stored_key(
        monkeypatch, stored_key):
    stored_key(None)
    monkeypatch.delenv("PUFFPAI_API_KEY", raising=False)
    file_key = "7c" * 32
    api_server._write_key_file(api_server._api_key_path(), file_key)
    assert api_server._load_or_create_api_key() == file_key


# ── 5. the helper's own contract ────────────────────────────────────────────

def test_explicit_env_api_key_returns_none_when_unset(monkeypatch):
    monkeypatch.delenv("PUFFPAI_API_KEY", raising=False)
    assert api_server._explicit_env_api_key() is None
