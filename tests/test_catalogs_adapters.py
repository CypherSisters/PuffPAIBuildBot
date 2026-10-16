"""Tests for the outside-list readers: APIs.guru, public-apis, Home Assistant, MCP.

Each reader turns an outside file into short rows and then into Candidates.
What must hold for all of them: odd input is skipped, never guessed; outside
text is cleaned; nothing is marked keyless, local or licensed without the
file saying so; and a refresh writes a dated snapshot with provenance through
the fetch it is given (no socket in these tests).

Run:
    pytest tests/test_catalogs_adapters.py
"""
from __future__ import annotations

import hashlib
import json

import pytest

from puffpai.catalogs import apis_guru, ha_integrations, live, mcp_registry, public_apis


@pytest.fixture
def snaps(tmp_path, monkeypatch):
    shipped = tmp_path / "shipped"
    state = tmp_path / "state"
    shipped.mkdir()
    state.mkdir()
    monkeypatch.setattr(live, "shipped_snapshot_dir", lambda: str(shipped))
    monkeypatch.setattr(live, "state_snapshot_dir", lambda: str(state))
    live.clear_caches()
    yield str(state)
    live.clear_caches()


def _fake_fetch(pages):
    """A fetch that serves canned bodies by URL and records what it was asked."""
    asked = []

    def fetch(url, *, hosts, max_bytes=0, accept="application/json", **_kw):
        asked.append((url, set(hosts)))
        body = pages[url] if url in pages else pages["*"]
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        return live.FetchResult(url=url, final_url=url, status=200, body=body,
                                sha256=hashlib.sha256(body).hexdigest(),
                                fetched_at="2026-09-23T00:00:00+00:00", content_type="")
    return fetch, asked


# ---------------------------------------------------------------------------
# APIs.guru
# ---------------------------------------------------------------------------

LIST = {
    "example.org:rocks": {
        "preferred": "1.0",
        "versions": {"1.0": {
            "info": {"title": "Rock Facts", "description": "<p>Facts about <b>rocks</b></p> [x](https://y)",
                     "x-apisguru-categories": ["open_data"], "license": {"name": "MIT"},
                     "contact": {"url": "https://example.org/rocks"}},
            "swaggerUrl": "https://api.apis.guru/v2/specs/example.org/rocks/1.0/openapi.json"}},
    },
    "bad.example:spec": {
        "preferred": "1",
        "versions": {"1": {"info": {"title": "Elsewhere"},
                           "swaggerUrl": "https://evil.example.net/spec.json"}},
    },
    "broken": "not a dict",
}


def test_apis_guru_trim_keeps_only_safe_fields():
    rows = {r["id"]: r for r in apis_guru.trim(LIST)}
    rock = rows["example.org:rocks"]
    assert rock["description"] == "Facts about rocks x"
    assert rock["license"] == "MIT" and rock["categories"] == ["open_data"]
    assert rock["spec_url"].startswith("https://api.apis.guru/")
    assert rows["bad.example:spec"]["spec_url"] is None  # spec must live on api.apis.guru
    assert "broken" not in rows


def test_apis_guru_candidates_start_as_unknown_key(snaps):
    live.write_snapshot("apis_guru", apis_guru.trim(LIST), provenance={}, out_dir=snaps)
    cand = [c for c in apis_guru.candidates() if c["id"] == "apis_guru:example.org:rocks"][0]
    assert cand["auth"] == "unknown" and "key" in cand["needs"]
    assert cand["sample_endpoint"] is None and cand["host"] is None and cand["verified_at"] is None
    assert "rock" in cand["match_terms"]


OPEN_SPEC = {
    "openapi": "3.0.0",
    "servers": [{"url": "https://rocks.example.org/v1"}],
    "paths": {
        "/rocks/{id}": {"get": {}},
        "/search": {"get": {"parameters": [{"name": "q", "in": "query", "required": True}]}},
        "/ref": {"get": {"parameters": [{"$ref": "#/components/parameters/X"}]}},
        "/latest": {"get": {"parameters": [{"name": "limit", "in": "query"}]}},
    },
}


def test_read_spec_builds_a_sample_only_for_a_keyless_parameter_free_get():
    facts = apis_guru.read_spec(OPEN_SPEC)
    assert facts["auth"] == "none"
    assert facts["host"] == "rocks.example.org"
    assert facts["sample_endpoint"] == "https://rocks.example.org/v1/latest"
    assert facts["get_count"] == 4


def test_read_spec_with_a_key_scheme_builds_no_sample():
    spec = dict(OPEN_SPEC, components={"securitySchemes": {"k": {"type": "apiKey"}}})
    facts = apis_guru.read_spec(spec)
    assert facts["auth"] == "unknown" and facts["sample_endpoint"] is None
    assert facts["scheme_kinds"] == ["apiKey"]


def test_read_spec_refuses_templated_http_and_lan_servers():
    for url in ("https://{region}.example.org", "http://rocks.example.org", "https://10.1.1.1/v1"):
        facts = apis_guru.read_spec(dict(OPEN_SPEC, servers=[{"url": url}]))
        assert facts["sample_endpoint"] is None, url
    swagger = {"swagger": "2.0", "host": "old.example.org", "basePath": "/api", "schemes": ["https"],
               "paths": {"/all": {"get": {}}}}
    assert apis_guru.read_spec(swagger)["sample_endpoint"] == "https://old.example.org/api/all"
    assert apis_guru.read_spec("junk")["auth"] == "unknown"


def test_enrich_reads_the_spec_from_apis_guru_only(snaps):
    live.write_snapshot("apis_guru", apis_guru.trim(LIST), provenance={}, out_dir=snaps)
    cand = [c for c in apis_guru.candidates() if c["id"] == "apis_guru:example.org:rocks"][0]
    fetch, asked = _fake_fetch({"*": OPEN_SPEC})
    updated = apis_guru.enrich(cand, fetch=fetch)
    assert asked[0][1] == {"api.apis.guru"}
    assert updated["auth"] == "none" and updated["needs"] == []
    assert updated["sample_endpoint"] == "https://rocks.example.org/v1/latest"
    assert updated["verified_at"] is None  # built, never called
    assert cand["sample_endpoint"] is None  # input not modified


def test_apis_guru_refresh_writes_a_dated_snapshot_with_provenance(snaps, monkeypatch):
    monkeypatch.setattr(live, "refresh_refusal", lambda: None)
    fetch, asked = _fake_fetch({"*": LIST})
    result = live.refresh("apis_guru", out_dir=snaps, fetch=fetch)
    assert result["ok"] and result["count"] == 2
    snap = live.latest_snapshot("apis_guru")
    assert snap["provenance"]["sha256"] and snap["provenance"]["source_url"] == apis_guru.LIST_URL
    assert snap["with_license"] == 1


# ---------------------------------------------------------------------------
# public-apis
# ---------------------------------------------------------------------------

README = """
# Public APIs
| Sponsor | [Big Co](https://bigco.example/?utm_source=x) | Paid thing | `apiKey` | Yes | Yes |

## Index
### Animals
API | Description | Auth | HTTPS | CORS
|:---|:---|:---|:---|:---|
| [Cat Facts](https://catfact.example/) | Daily <i>cat</i> facts | No | Yes | No |
| [Dog Keys](https://dogs.example/) | Dogs, with a key | `apiKey` | Yes | Yes |
| [Old Birds](http://birds.example/) | Birds over http | No | No | Unknown |
| [Tracked](https://t.example/?utm_campaign=z) | Has tracking | No | Yes | Yes |
not a row
### Science & Math
| [Cat Facts](https://catfact2.example/) | Same name again | No | Yes | Yes |
"""


def test_public_apis_parse_skips_the_sponsor_block_and_tracking_links():
    rows = public_apis.parse(README)
    names = [r["name"] for r in rows]
    assert "Big Co" not in names and "Tracked" not in names
    assert names == ["Cat Facts", "Dog Keys", "Old Birds", "Cat Facts"]
    assert rows[0]["description"] == "Daily cat facts" and rows[0]["auth"] == "no"
    assert rows[1]["auth"] == "apikey"
    assert rows[2]["url"] is None  # http link not kept


def test_public_apis_candidates_are_keyless_https_only(snaps):
    live.write_snapshot("public_apis", public_apis.parse(README), provenance={}, out_dir=snaps)
    cands = public_apis.candidates()
    assert [c["id"] for c in cands] == ["public_apis:cat_facts", "public_apis:cat_facts_2"]
    for cand in cands:
        assert cand["auth"] == "none" and cand["sample_endpoint"] is None
        assert cand["license"].startswith("not stated") and cand["host"] is None


# ---------------------------------------------------------------------------
# Home Assistant
# ---------------------------------------------------------------------------

HA = {
    "integration": {
        "sonos": {"name": "Sonos", "integration_type": "hub", "iot_class": "local_push"},
        "abode": {"name": "Abode", "integration_type": "hub", "iot_class": "cloud_push"},
        "recorder": {"name": "Recorder", "integration_type": "system", "iot_class": "local_push"},
        "airthings": {"name": "Airthings", "integrations": {
            "airthings": {"name": "Airthings", "integration_type": "hub", "iot_class": "cloud_polling"},
            "airthings_ble": {"name": "Airthings BLE", "integration_type": "device", "iot_class": "local_polling"},
        }},
        "3_day_blinds": {"name": "3 Day Blinds", "integration_type": "virtual", "supported_by": "motion_blinds"},
        "motion_blinds": {"name": "Motion Blinds", "integration_type": "hub", "iot_class": "local_push"},
        "ikea_zig": {"name": "Zig Bulb", "integration_type": "virtual", "iot_standards": ["zigbee"]},
        "mystery": {"name": "Mystery"},
    },
    "helper": {"counter": {"integration_type": "helper"}},
}


def test_ha_trim_flattens_brands_and_skips_home_assistant_internals():
    rows = {r["domain"]: r for r in ha_integrations.trim(HA)}
    assert "recorder" not in rows and "counter" not in rows
    assert rows["airthings_ble"]["brand"] == "Airthings"
    assert rows["3_day_blinds"]["iot_class"] == "local_push"
    assert rows["3_day_blinds"]["supported_by_name"] == "Motion Blinds"
    assert ha_integrations.trim("junk") == []


def test_ha_candidates_say_where_they_run(snaps):
    live.write_snapshot("ha_integrations", ha_integrations.trim(HA), provenance={}, out_dir=snaps)
    by_id = {c["id"]: c for c in ha_integrations.candidates()}
    assert by_id["ha:sonos"]["local_or_cloud"] == "local"
    assert by_id["ha:abode"]["local_or_cloud"] == "cloud" and by_id["ha:abode"]["auth"] == "unknown"
    assert by_id["ha:mystery"]["local_or_cloud"] == "unknown"
    assert by_id["ha:ikea_zig"]["local_or_cloud"] == "local" and "Zigbee" in by_id["ha:ikea_zig"]["what_it_is"]
    blinds = by_id["ha:3_day_blinds"]
    assert "Motion Blinds connection" in blinds["what_it_is"] and "motion_blinds" not in blinds["what_it_is"]
    assert ha_integrations.setup_domain(blinds) == "motion_blinds"
    for cand in by_id.values():
        assert cand["needs"] == ["home_assistant"] and cand["kind"] == "integration"


# ---------------------------------------------------------------------------
# MCP Registry
# ---------------------------------------------------------------------------

def _server(name, remotes=(), packages=(), status="active", **extra):
    server = {"name": name, "description": f"{name} tool", "version": "1",
              "remotes": [{"type": "streamable-http", "url": u} for u in remotes],
              "packages": [{"registryType": p} for p in packages]}
    server.update(extra)
    return {"server": server,
            "_meta": {"io.modelcontextprotocol.registry/official": {"status": status}}}


def test_mcp_trim_labels_remote_and_code_servers():
    remote = mcp_registry.to_candidate(mcp_registry.trim_server(
        _server("io.example/weather", remotes=["https://mcp.weather.example/x"])))
    assert "sends your data to mcp.weather.example" in remote["what_it_is"]
    assert remote["local_or_cloud"] == "cloud" and remote["needs"] == []
    code = mcp_registry.to_candidate(mcp_registry.trim_server(_server("io.example/files", packages=["npm"])))
    assert code["needs"] == ["code_sandbox"]
    assert "cannot run outside programs safely yet" in code["what_it_is"]
    assert "sandbox" not in code["what_it_is"]
    assert code["license"].startswith("not stated") and code["auth"] == "unknown"
    assert mcp_registry.trim_server(_server("io.example/old", status="deprecated")) is None
    lan = mcp_registry.trim_server(_server("io.example/lan", remotes=["https://192.168.1.2/mcp"]))
    assert lan["remote_hosts"] == []


def test_mcp_refresh_pages_and_marks_a_partial_list(snaps, monkeypatch):
    monkeypatch.setattr(live, "refresh_refusal", lambda: None)
    page1 = {"servers": [_server("a.example/one")], "metadata": {"nextCursor": "c2"}}
    page2 = {"servers": [_server("b.example/two")], "metadata": {"nextCursor": "c3"}}
    fetch, asked = _fake_fetch({mcp_registry.page_url(None): page1,
                                mcp_registry.page_url("c2"): page2, "*": {"servers": []}})
    result = mcp_registry.refresh(out_dir=snaps, fetch=fetch, max_pages=2)
    assert result["ok"] and result["count"] == 2 and "part of the list" in result["message"]
    snap = live.latest_snapshot("mcp_registry")
    assert snap["complete"] is False and snap["provenance"]["pages"] == 2
    assert all(h == {"registry.modelcontextprotocol.io"} for _u, h in asked)
    done = {"servers": [_server("c.example/three")], "metadata": {}}
    fetch2, _ = _fake_fetch({"*": done})
    assert mcp_registry.refresh(out_dir=snaps, fetch=fetch2)["message"] == ""


def test_mcp_own_description_drops_our_notes():
    code = mcp_registry.to_candidate(mcp_registry.trim_server(
        _server("io.example/files", packages=["npm"], remotes=["https://mcp.files.example/x"])))
    assert code["what_it_is"].startswith("It sends your data to mcp.files.example.")
    own = mcp_registry.own_description(code["what_it_is"])
    assert own == "io.example/files tool"
    bare = mcp_registry.to_candidate(mcp_registry.trim_server(_server("io.example/bare")))
    assert bare["what_it_is"].startswith(mcp_registry.UNKNOWN_NOTE)
    assert mcp_registry.own_description(bare["what_it_is"]) == "io.example/bare tool"


def test_mcp_rows_that_call_themselves_deprecated_are_skipped(snaps, monkeypatch):
    items = [mcp_registry.trim_server(_server("io.example/old-garden", description="DEPRECATED. Use x.")),
             mcp_registry.trim_server(_server("io.example/garden", description="Garden beds planner."))]
    live.write_snapshot("mcp_registry", [i for i in items if i],
                        provenance={"source_url": "x"}, out_dir=live.state_snapshot_dir())
    live.clear_caches()
    ids = [c["id"] for c in mcp_registry.candidates()]
    assert ids == ["mcp:io.example/garden"]
