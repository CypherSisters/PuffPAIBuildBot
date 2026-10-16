"""Tests for GET /build/self - the endpoint the workbench landing view calls.

The screen that opens when an owner logs in has to say what THIS build is:
three zones, crew, device, knowledge, surfaces, and the honest limits summary.
So the questions worth asking here are:

* it is behind the same app API key as its ``/setup/*`` neighbours, with no new
  public endpoint and no locally re-implemented check;
* it reports this build's real capabilities, straight from self_knowledge;
* it leaks no PIN, no recovery code, and no token, even when a hand-edited
  manifest carries one;
* an unconfigured build is reported as unconfigured, not as an empty body -
  the caller must be able to tell "not set up" from "backend said nothing";
* an empty surface allowlist reads as "not trimmed", never as "nothing allowed".

Run:
    pytest tests/test_build_self_api.py
"""
from __future__ import annotations

import os
import tempfile

# Isolate on-disk state BEFORE importing the app: module-level singletons in
# api_server resolve their paths at import time (same pattern as test_api.py).
_TMP_DATA_DIR = tempfile.mkdtemp(prefix="puffpai_build_self_api_test_")
os.environ["PUFFPAI_DATA_DIR"] = _TMP_DATA_DIR

import json  # noqa: E402

import pytest  # noqa: E402

from puffpai import api_server, build_manifest, self_knowledge  # noqa: E402
from puffpai.config import get_config  # noqa: E402

API_KEY = api_server._API_KEY


def _auth():
    return {"Authorization": f"Bearer {API_KEY}"}


@pytest.fixture
def client():
    api_server.app.config.update(TESTING=True)
    return api_server.app.test_client()


@pytest.fixture
def scratch(tmp_path):
    """Isolated data dir for one test, restored afterwards."""
    prev = os.environ.get("PUFFPAI_DATA_DIR")
    os.environ["PUFFPAI_DATA_DIR"] = str(tmp_path)
    get_config(_force_reload=True)
    try:
        yield tmp_path
    finally:
        if prev is not None:
            os.environ["PUFFPAI_DATA_DIR"] = prev
        else:
            os.environ.pop("PUFFPAI_DATA_DIR", None)
        get_config(_force_reload=True)


def _write_manifest(manifest):
    path = build_manifest.manifest_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh)
    return path


def _space_bot_manifest():
    """A built space bot: trimmed to a telescope and an offline library."""
    census = {
        "schema": "hardware_census/v1",
        "memory": {"total_gb": 16.0, "available_gb": 8.0, "source": "stated"},
        "cpu": {"model": "Test CPU", "arch": "x86_64", "logical_cores": 8,
                "source": "detected"},
        "accelerator": {"kind": "none", "name": None, "vram_gb": None,
                        "source": "detected"},
        "soc": {"model": None, "is_jetson": False},
        "disk": {"total_gb": 500.0, "free_gb": 200.0},
        "hardware_class": "workstation",
        "model_budget_gb": 9.0,
        "notes": [],
    }
    manifest = build_manifest.assemble_manifest(
        census,
        identity={"identity": "custom", "name": "Orrery"},
        brain={"model": "llama3.2:3b", "recommended": "llama3.2:3b",
               "chosen_by": "owner"},
        crew={"master_core": 7, "max_specialists": 2,
              "selected_specialists": ["astronomer"]},
        surfaces={
            "allowlist": ["instruments.telescope", "knowledge.library"],
            "reasons": {"instruments.telescope": "your telescope feed"},
            "sensitive": [],
            "derived_from_goal": True,
            "owner_edited": False,
            "note": "Surfaces outside the allowlist are not loaded.",
        },
        posture={
            "build_network": "offline", "build_network_source": "stated",
            "run_egress": "sealed", "run_egress_source": "stated",
            "egress_switches": {"weather": False, "web_search": False},
            "enabled_switches": [], "combination": "sealed",
        },
    )
    manifest["vault"] = {
        "configured": True, "sealed": True, "method": "pin",
        "recovery_issued": True, "recovery_confirmed": True,
        "recovery_escrow": "wrapped-key", "configured_at": 1.0,
    }
    manifest["bridge"] = {
        "posture": "closed", "label": "Closed", "summary": "Nothing goes out.",
        "allows": {}, "costs": "", "strictness": 3, "run_egress": "sealed",
        "requested_posture": "closed", "reconciled": False, "note": "",
        "environment": {}, "chosen_by": "owner", "enforced_by": "Drawbridge",
        "configured_at": 1.0,
    }
    return manifest


# -- The gate: exactly the /setup/* pattern -----------------------------------

def test_the_endpoint_requires_the_app_api_key(client):
    assert client.get("/build/self").status_code == 401


def test_the_api_key_is_accepted(client, scratch):
    assert client.get("/build/self", headers=_auth()).status_code == 200


def test_the_endpoint_was_not_made_public():
    """The gate is default-deny; nothing here belongs on the exception list."""
    for rule in api_server.app.url_map.iter_rules():
        if str(rule.rule) == "/build/self":
            assert rule.endpoint not in api_server._PUBLIC_ENDPOINTS
            break
    else:
        pytest.fail("/build/self is not registered")


def test_the_key_check_is_the_shared_hook_not_a_local_one():
    assert api_server._check_api_key in api_server.app.before_request_funcs[None]


# -- The contract -------------------------------------------------------------

def test_it_reports_this_builds_real_capabilities(client, scratch):
    _write_manifest(_space_bot_manifest())
    body = client.get("/build/self", headers=_auth()).get_json()

    expected = self_knowledge.capabilities()
    assert body["capabilities"]["available"] == expected["available"]
    assert body["capabilities"]["summary"] == expected["summary"]
    assert body["capabilities"]["trimmed"] is True
    # Not a hand-rolled second opinion: the surfaces block reads trimming off
    # the same self_knowledge answer.
    assert body["surfaces"]["trimmed"] is body["capabilities"]["trimmed"]
    assert body["surfaces"]["allowlist"] == [
        "instruments.telescope", "knowledge.library"]

    # The contract the dashboard's widget gate reads: a space bot's meals and
    # workout surfaces are not loaded, so those widgets do not belong on its
    # screen, and the payload says why in plain words.
    for persona_surface in ("daily.kitchen", "daily.fitness", "house.cameras"):
        assert persona_surface not in body["surfaces"]["allowlist"]
        assert persona_surface not in body["capabilities"]["available"]
        assert body["capabilities"]["unavailable"][persona_surface]


def test_it_reports_the_zones_the_crew_and_the_device(client, scratch):
    _write_manifest(_space_bot_manifest())
    body = client.get("/build/self", headers=_auth()).get_json()

    assert body["configured"] is True
    assert body["build"]["name"] == "Orrery"

    assert body["zones"]["vault"]["configured"] is True
    assert body["zones"]["vault"]["recovery_confirmed"] is True
    assert body["zones"]["brain"]["model"] == "llama3.2:3b"
    assert body["zones"]["brain"]["routing_tier"]
    assert body["zones"]["bridge"]["posture"] == "closed"
    assert body["zones"]["bridge"]["run_egress"] == "sealed"

    assert body["crew"]["configured"] is True
    assert body["crew"]["max_specialists"] == 2
    assert body["crew"]["selected_specialists"] == ["astronomer"]

    assert body["device"]["source"] == "manifest"
    assert body["device"]["total_ram_gb"] == 16.0
    assert body["device"]["hardware_class"] == "workstation"


def test_an_unconfigured_build_is_reported_as_unconfigured_not_empty(client, scratch):
    resp = client.get("/build/self", headers=_auth())
    assert resp.status_code == 200
    body = resp.get_json()

    assert body["configured"] is False
    assert body["zones"]["vault"]["configured"] is False
    assert body["zones"]["brain"]["configured"] is False
    assert body["zones"]["bridge"]["configured"] is False
    assert body["crew"]["configured"] is False
    # Unconfigured, but not blank: the build still says what it is, and the
    # device is looked at rather than reported as zeros.
    assert body["capabilities"]["summary"]
    assert body["surfaces"]["taxonomy"]
    assert body["device"]["source"] == "live"


def test_an_untrimmed_build_is_not_trimmed_and_carries_everything(client, scratch):
    """The trap: an empty allowlist means "not trimmed", never "nothing"."""
    census = {"hardware_class": "workstation", "memory": {"total_gb": 16.0},
              "cpu": {}, "accelerator": {"kind": "none"}}
    _write_manifest(build_manifest.assemble_manifest(census))

    body = client.get("/build/self", headers=_auth()).get_json()
    assert body["surfaces"]["allowlist"] == []
    assert body["surfaces"]["trimmed"] is False
    # Nothing is left out of a stock build: no page is "not loaded". But a page
    # is only CLAIMED when something stands behind it (build_abilities); the
    # telescope page is a name and an icon on every build today.
    caps = body["capabilities"]
    taxonomy = set(body["surfaces"]["taxonomy"])
    assert set(caps["available"]) | set(caps["unavailable"]) == taxonomy
    assert "instruments.telescope" in caps["unavailable"]
    assert not any("left out" in why for why in caps["unavailable"].values())


# -- Secrets ------------------------------------------------------------------

def test_the_payload_carries_no_pin_recovery_code_or_token(client, scratch):
    """Even a hand-edited manifest holding secrets cannot leak through here."""
    manifest = _space_bot_manifest()
    manifest["vault"]["vault_pin"] = "8213"
    manifest["vault"]["vault_recovery_code"] = "AAAA-BBBB-CCCC-DDDD"
    manifest["integrations"] = {"home_assistant": True, "ha_token": "secret-ha-token",
                                "media_server": False, "devices": []}
    manifest["api_key"] = "secret-api-key"
    _write_manifest(manifest)

    raw = client.get("/build/self", headers=_auth()).get_data(as_text=True)

    for planted in ("8213", "AAAA-BBBB-CCCC-DDDD", "secret-ha-token",
                    "secret-api-key"):
        assert planted not in raw

    body = json.loads(raw)

    def walk(node, path=""):
        if isinstance(node, dict):
            for key, value in node.items():
                assert key not in build_manifest._SECRET_KEYS, \
                    f"{path}.{key} is a secret key name"
                walk(value, f"{path}.{key}")
        elif isinstance(node, list):
            for i, value in enumerate(node):
                walk(value, f"{path}[{i}]")

    walk(body)
    # The app's own API key is never echoed back to the caller.
    assert API_KEY not in raw
