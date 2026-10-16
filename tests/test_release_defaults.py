"""Release gate: what a freshly-cloned BuildBot does before anyone configures it.

This repository goes public, and the defaults in it are what most people will
ever run. So the shipped state has to be the shut state:

* the example config binds loopback, not every interface on the network;
* the default run posture is ``island`` — no outbound calls;
* every enumerated egress door starts shut;
* no shipped config file carries a real secret;
* the API stays default-deny (the key check is a global hook, and the public
  exception list is small and inspectable).

Run:
    pytest tests/test_release_defaults.py
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from puffpai import api_server, build_posture

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = REPO_ROOT / "config"


def _load(name: str) -> dict:
    return json.loads((CONFIG_DIR / name).read_text(encoding="utf-8"))


# ── bind address ────────────────────────────────────────────────────────────

def test_the_example_config_binds_loopback_only():
    cfg = _load("puffpai_config.example.json")
    assert cfg["api_server"]["host"] == "127.0.0.1", (
        "a fresh clone must not serve the API to the whole LAN")


def test_the_code_default_binds_loopback_too():
    """Deleting the config file must not widen the bind address."""
    from puffpai.config import ApiServerConfig
    assert ApiServerConfig().host == "127.0.0.1"


def test_no_shipped_config_binds_all_interfaces():
    for path in CONFIG_DIR.rglob("*.json"):
        text = path.read_text(encoding="utf-8")
        assert "0.0.0.0" not in text, f"{path.name} binds every interface"


# ── egress ──────────────────────────────────────────────────────────────────

def test_the_default_run_egress_is_island():
    assert build_posture.DEFAULT_RUN_EGRESS == "island"


def test_the_island_option_is_the_one_marked_default_in_the_menu():
    defaults = [o["id"] for o in build_posture.run_egress_options() if o["default"]]
    assert defaults == ["island"]


def test_every_enumerated_door_starts_shut():
    doors = build_posture.egress_switches()
    assert doors, "the door table is empty; this guard would pass vacuously"
    for door in doors:
        assert door["enabled"] is False, f"door {door['id']} ships open"
    assert set(build_posture.switch_ids()) == {d["id"] for d in doors}


def test_the_shipped_bridge_postures_open_no_door():
    """The postures file is copy only — it must not switch anything on.

    The door table itself lives in code (``build_posture._EGRESS_SWITCHES``),
    deliberately: an edit to this JSON can describe a posture but can never
    loosen one. The check here is that the shipped copy contains no enabled
    flag and does not default to the permissive posture.
    """
    data = _load("bridge_postures.json")
    assert data["default"] == "fenced"
    ids = {p["id"] for p in data["postures"]}
    assert "open_yard" in ids and data["default"] != "open_yard"
    assert '"enabled": true' not in json.dumps(data).lower()
    for posture in data["postures"]:
        if posture["id"] == "open_yard":
            assert posture["recommendable"] is False


# ── secrets ─────────────────────────────────────────────────────────────────

_SECRETISH = re.compile(
    r"(token|secret|password|passwd|api_key|apikey|_key|credential)$", re.I)
_PLACEHOLDER = re.compile(
    r"^(paste[_-]?your|your[_-]|change[_-]?me|replace[_-]?me|placeholder|"
    r"example|<.*>|x{3,})", re.I)


def _secret_shaped(value: str) -> bool:
    """A value long enough to be a credential and not obviously a stand-in."""
    text = value.strip()
    if len(text) < 16:
        return False
    return not _PLACEHOLDER.match(text)


def test_no_shipped_config_file_carries_a_real_secret():
    offenders: list[str] = []

    def walk(node, path, source):
        if isinstance(node, dict):
            for key, value in node.items():
                if (isinstance(value, str) and _SECRETISH.search(key)
                        and _secret_shaped(value)):
                    # The NAME of the offending field only — never the value.
                    offenders.append(f"{source}:{path}.{key}")
                walk(value, f"{path}.{key}", source)
        elif isinstance(node, list):
            for i, value in enumerate(node):
                walk(value, f"{path}[{i}]", source)

    for path in CONFIG_DIR.rglob("*.json"):
        walk(json.loads(path.read_text(encoding="utf-8")), "", path.name)
    assert offenders == [], f"secret-shaped values in shipped config: {offenders}"


def test_the_shipped_config_examples_are_examples():
    """The two files that hold credentials ship with placeholders, not blanks
    that look configured and not values that look real."""
    assert _load("home_assistant_config.example.json")["token"].startswith("PASTE")
    assert _load("media_server_config.example.json")["jellyfin_api_key"].startswith("PASTE")


# ── default-deny ────────────────────────────────────────────────────────────

def test_the_api_key_check_is_a_global_before_request_hook():
    """Default-deny lives in one hook; per-endpoint checks would drift."""
    assert api_server._check_api_key in api_server.app.before_request_funcs[None]


def test_the_public_exception_list_stays_small_and_explicit():
    public = api_server._PUBLIC_ENDPOINTS
    assert isinstance(public, frozenset)
    assert len(public) <= 12, (
        "every addition here is an endpoint reachable with no key at all")
    for sensitive in ("vault", "documents", "app_data", "ha_service",
                      "build_self", "setup"):
        assert not any(sensitive in name for name in public), (
            f"a {sensitive} endpoint is on the no-key list")


@pytest.mark.parametrize("route", ["/build/self", "/vault/contents"])
def test_a_keyless_request_is_refused(route):
    api_server.app.config.update(TESTING=True)
    client = api_server.app.test_client()
    assert client.get(route).status_code == 401
