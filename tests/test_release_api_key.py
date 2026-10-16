"""Release gate: the app API key must be a real secret, kept out of the tree.

Two failures this file exists to prevent, both of which ship badly in a PUBLIC
repository:

* the plaintext fallback key landing INSIDE the checkout
  (``<repo>/data/api_key.txt``), where it is one ``git add`` from publication;
* the backend starting on a placeholder ("CHANGE_ME", "changeme", a 4-character
  string), which would give every box built from this repo the same "secret" in
  front of the household's documents, photos, vault status and local model.

Run:
    pytest tests/test_release_api_key.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from puffpai import api_server
from puffpai.config import get_state_dir

REPO_ROOT = Path(__file__).resolve().parents[1]


# ── what the backend refuses to start on ────────────────────────────────────

# None of these is a credential; each is what a half-finished install leaves.
REFUSED = [
    "", "   ", "\n",
    "CHANGE_ME", "change_me", "ChAnGe_Me", "changeme", "change-me",
    "YOUR_API_KEY_HERE", "your-api-key-here",
    "REPLACE_ME", "placeholder", "secret", "test",
    "paste-your-backend-api-key-here",
    "short", "0123456789abcde",  # 15 chars: one under the floor
]


@pytest.mark.parametrize("bad", REFUSED)
def test_placeholder_and_short_keys_refuse_to_start(bad):
    with pytest.raises(RuntimeError) as err:
        api_server.validate_api_key(bad)
    message = str(err.value)
    assert "refusing to start" in message
    # The message has to be actionable without quoting the value.
    assert "PUFFPAI_API_KEY" in message
    # The value is never quoted back. (A bare substring check would trip on
    # ordinary words like "secret" that the advice itself uses, so the test
    # asserts the thing that matters: the value is not echoed as a value.)
    value = bad.strip()
    if value:
        assert f"'{value}'" not in message
        assert f'"{value}"' not in message
        assert repr(value) not in message


def test_none_is_refused():
    with pytest.raises(RuntimeError):
        api_server.validate_api_key(None)


def test_a_real_key_is_accepted_and_stripped():
    key = "a" * 16
    assert api_server.validate_api_key(f"  {key}\n") == key
    assert api_server.validate_api_key("9f" * 32) == "9f" * 32


def test_the_floor_is_sixteen_characters():
    assert api_server.MIN_API_KEY_LEN == 16
    assert api_server.validate_api_key("b" * 16) == "b" * 16


def test_the_running_key_went_through_the_validator():
    """Whatever this process resolved, it is not a placeholder."""
    assert len(api_server._API_KEY) >= api_server.MIN_API_KEY_LEN
    assert api_server._API_KEY.lower() not in api_server.PLACEHOLDER_API_KEYS


# ── where the plaintext fallback lives ──────────────────────────────────────

def test_the_key_file_is_not_inside_the_checkout():
    path = Path(api_server._api_key_path()).resolve()
    assert REPO_ROOT not in path.parents, (
        f"API key fallback {path} is inside the repository checkout")
    assert path.parent == Path(get_state_dir()).resolve()


def test_the_state_dir_follows_the_explicit_override(monkeypatch, tmp_path):
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(tmp_path / "pinned"))
    assert Path(get_state_dir()).resolve() == (tmp_path / "pinned").resolve()


def test_a_moved_data_dir_takes_the_state_dir_with_it(monkeypatch, tmp_path):
    monkeypatch.delenv("PUFFPAI_STATE_DIR", raising=False)
    monkeypatch.setenv("PUFFPAI_DATA_DIR", str(tmp_path))
    assert Path(get_state_dir()).resolve() == (tmp_path / "state").resolve()


def test_the_default_state_dir_is_outside_any_checkout(
        monkeypatch, allow_real_user_state_root):
    """With no overrides at all it is a per-user OS directory, not the repo.

    The only test that asks ``conftest`` to un-booby-trap the real resolver: the
    claim being proved is about that path, and it is only read.
    """
    monkeypatch.delenv("PUFFPAI_STATE_DIR", raising=False)
    monkeypatch.delenv("PUFFPAI_DATA_DIR", raising=False)
    path = Path(get_state_dir()).resolve()
    assert REPO_ROOT not in path.parents and path != REPO_ROOT
    assert "puffpai" in path.name.lower()


# ── how it is written ───────────────────────────────────────────────────────

def test_the_key_file_is_owner_only(monkeypatch, tmp_path):
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(tmp_path / "state"))
    path = api_server._api_key_path()
    api_server._write_key_file(path, "c" * 32)
    assert Path(path).read_text(encoding="utf-8") == "c" * 32
    if sys.platform != "win32":
        assert oct(os.stat(path).st_mode & 0o777) == "0o600"
    else:
        # Windows ignores the mode bits; the file relies on the profile ACL
        # plus a best-effort icacls grant, so all we can assert portably is
        # that it exists and the process can read it back.
        assert os.path.isfile(path)


# ── the one-time migration out of the checkout ──────────────────────────────

def test_a_legacy_in_tree_key_is_moved_and_the_old_copy_deleted(
        monkeypatch, tmp_path, capsys):
    legacy = tmp_path / "checkout" / "data" / "api_key.txt"
    legacy.parent.mkdir(parents=True)
    key = "d" * 40
    legacy.write_text(key, encoding="utf-8")
    monkeypatch.setattr(api_server, "_LEGACY_API_KEY_PATH", str(legacy))
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(tmp_path / "state"))

    assert api_server._migrate_legacy_key_file() == key
    assert not legacy.exists(), "the in-checkout copy must be gone"
    moved = Path(api_server._api_key_path())
    assert moved.read_text(encoding="utf-8") == key

    logged = capsys.readouterr().out
    assert "Moved the API key" in logged
    assert key not in logged, "the key value must never be printed"


def test_migration_is_a_no_op_when_there_is_nothing_to_migrate(
        monkeypatch, tmp_path):
    monkeypatch.setattr(
        api_server, "_LEGACY_API_KEY_PATH", str(tmp_path / "absent.txt"))
    assert api_server._migrate_legacy_key_file() is None


# ── the file stays ignored ──────────────────────────────────────────────────

def test_gitignore_still_covers_the_legacy_key_file():
    text = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "data/api_key.txt" in text or "\ndata/\n" in text
