"""Chat history lives in the state dir, not beside the code.

A relative ``paths.chat_history_dir`` used to resolve against ``REPO_ROOT``:
with ``PUFFPAI_STATE_DIR`` pinned elsewhere the server still loaded and wrote
``<checkout>/chat_history``, splitting an owner's data and sharing conversations
between every build run from one checkout.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

import json
import os
import types

import pytest

from puffpai import api_server, config


def _cfg_with(chat_history_dir):
    return types.SimpleNamespace(
        paths=types.SimpleNamespace(chat_history_dir=chat_history_dir))


def _write_conv(directory, conv_id):
    os.makedirs(directory, exist_ok=True)
    with open(os.path.join(directory, f"{conv_id}.json"), "w",
              encoding="utf-8") as f:
        json.dump({"id": conv_id, "created_at": "2026-01-01T00:00:00",
                   "messages": [{"role": "user", "content": "hi"}]}, f)


@pytest.fixture
def roots(tmp_path, monkeypatch):
    """A pinned state dir and a stand-in checkout, both under tmp."""
    state = tmp_path / "state"
    repo = tmp_path / "checkout"
    state.mkdir()
    repo.mkdir()
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(state))
    monkeypatch.setattr(config, "_find_repo_root", lambda: str(repo))
    monkeypatch.setattr(config, "get_config", lambda **_: _cfg_with("chat_history"))
    with api_server._chat_history_lock:
        saved = list(api_server._chat_history)
    yield types.SimpleNamespace(state=str(state), repo=str(repo))
    with api_server._chat_history_lock:
        api_server._chat_history[:] = saved


# ── the resolver ───────────────────────────────────────────────────────────

def test_relative_path_resolves_under_state_dir(roots):
    assert config.get_chat_history_dir() == os.path.join(roots.state, "chat_history")


def test_relative_path_is_not_under_the_checkout(roots):
    resolved = config.get_chat_history_dir()
    assert not os.path.normcase(resolved).startswith(os.path.normcase(roots.repo))


def test_data_dir_override_alone_keeps_chat_with_the_state(tmp_path, monkeypatch, roots):
    monkeypatch.delenv("PUFFPAI_STATE_DIR")
    monkeypatch.setenv("PUFFPAI_DATA_DIR", str(tmp_path / "data"))
    assert config.get_chat_history_dir() == str(
        tmp_path / "data" / "state" / "chat_history")


def test_absolute_config_value_is_honoured(tmp_path, roots):
    explicit = str(tmp_path / "elsewhere" / "chats")
    assert config.get_chat_history_dir(explicit) == explicit
    assert config.legacy_chat_history_dir(explicit) is None


def test_empty_config_value_falls_back_to_default_name(roots):
    assert config.get_chat_history_dir("") == os.path.join(roots.state, "chat_history")


def test_legacy_dir_is_the_old_repo_root_location(roots):
    assert config.legacy_chat_history_dir() == os.path.join(roots.repo, "chat_history")


def test_legacy_dir_is_none_when_it_is_the_same_directory(roots, monkeypatch):
    monkeypatch.setenv("PUFFPAI_STATE_DIR", roots.repo)
    assert config.legacy_chat_history_dir() is None


# ── the server ─────────────────────────────────────────────────────────────

def test_server_saves_into_state_dir_and_not_the_checkout(roots):
    api_server._save_conversation({"id": "abc", "messages": []})
    assert os.path.isfile(os.path.join(roots.state, "chat_history", "abc.json"))
    assert not os.path.exists(os.path.join(roots.repo, "chat_history"))


def test_server_honours_absolute_config_value(tmp_path, monkeypatch, roots):
    explicit = str(tmp_path / "elsewhere" / "chats")
    monkeypatch.setattr(config, "get_config", lambda **_: _cfg_with(explicit))
    api_server._save_conversation({"id": "abc", "messages": []})
    assert os.path.isfile(os.path.join(explicit, "abc.json"))
    assert not os.path.exists(os.path.join(roots.state, "chat_history", "abc.json"))


def test_load_ignores_legacy_conversations_and_says_where_they_are(roots, capsys):
    legacy = os.path.join(roots.repo, "chat_history")
    _write_conv(legacy, "old-one")

    api_server._load_chat_history()

    assert api_server._chat_history == []
    out = capsys.readouterr().out
    assert out.count(legacy) == 1
    assert os.path.join(roots.state, "chat_history") in out
    # left exactly where it was, and nothing copied across
    assert os.listdir(legacy) == ["old-one.json"]
    assert os.listdir(os.path.join(roots.state, "chat_history")) == []


def test_no_legacy_note_once_the_state_dir_has_conversations(roots, capsys):
    legacy = os.path.join(roots.repo, "chat_history")
    _write_conv(legacy, "old-one")
    _write_conv(os.path.join(roots.state, "chat_history"), "new-one")

    api_server._load_chat_history()

    assert [c["id"] for c in api_server._chat_history] == ["new-one"]
    assert legacy not in capsys.readouterr().out


def test_no_legacy_note_when_there_is_nothing_legacy(roots, capsys):
    api_server._load_chat_history()
    assert "Older conversations" not in capsys.readouterr().out


def test_no_legacy_note_for_absolute_config_value(tmp_path, monkeypatch, roots, capsys):
    _write_conv(os.path.join(roots.repo, "chat_history"), "old-one")
    monkeypatch.setattr(config, "get_config",
                        lambda **_: _cfg_with(str(tmp_path / "elsewhere")))
    api_server._load_chat_history()
    assert "Older conversations" not in capsys.readouterr().out


# ── the desktop window reads the same files ────────────────────────────────

def test_desktop_app_state_defaults_to_the_same_directory(roots):
    from puffpai.ui.app_state import AppState
    assert AppState().chat_history_dir == os.path.join(roots.state, "chat_history")
