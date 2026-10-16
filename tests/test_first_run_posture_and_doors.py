"""Pre-flip findings, each with the case that refuted it.

NEW-1/W1-e  the wizard's chosen door was recorded in the manifest and shut at
            runtime, because the config seeded from the example carried an
            explicit ``egress_doors.weather: false`` that outranked it.
LAN-e       on a FIRST run there is no manifest, the write-path posture check
            read that as ``island``, and a public/LAN URL was accepted on a box
            whose owner had just answered ``sealed``.
LAN-c       ``lan_services`` read ``"ok"`` while two bad URLs sat in the config
            file, because neutralisation had cleaned the in-memory copy.
NEW-6       documentation and benchmark ranges classified as private, so a
            config could point at 203.0.113.5 under ``island``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from puffpai import build_posture, config as config_mod, setup_ops
from puffpai.security import egress_doors, lan_posture


@pytest.fixture(autouse=True)
def _clean():
    lan_posture.clear_active_posture()
    lan_posture.record_load_violations([])
    egress_doors.reset_cache()
    yield
    lan_posture.clear_active_posture()
    lan_posture.record_load_violations([])
    egress_doors.reset_cache()


@pytest.fixture
def box(tmp_path, monkeypatch):
    """A temp install: config dir, optional manifest, real config singleton."""
    def _make(run_egress: str | None = None, main: dict | None = None,
              doors: list[str] | None = None):
        cfg_dir = tmp_path / "config"
        cfg_dir.mkdir(exist_ok=True)
        (cfg_dir / "puffpai_config.json").write_text(
            json.dumps(main if main is not None else {}), encoding="utf-8")
        if run_egress is not None:
            block = build_posture.posture_manifest_block(
                build_posture.assess_posture("online", run_egress, doors))
            (cfg_dir / "build_manifest.json").write_text(
                json.dumps({"schema": "build_manifest/v2", "posture": block}),
                encoding="utf-8")
        monkeypatch.setenv("PUFFPAI_DATA_DIR", str(tmp_path))
        egress_doors.reset_cache()
        return config_mod.get_config(_force_reload=True), cfg_dir

    yield _make
    monkeypatch.undo()
    config_mod.get_config(_force_reload=True)
    egress_doors.reset_cache()


# ══ NEW-1 / W1-e ════════════════════════════════════════════════════════════

def test_the_example_config_does_not_ship_explicit_door_keys() -> None:
    """The trap itself: an explicit false in the seed outranks the manifest.

    The shipped example must leave the switch keys ABSENT so a manifest that
    says a door is open is what decides. Absent still means shut -- the door's
    own default is False -- so this loosens nothing.
    """
    example = json.loads(
        (Path(__file__).resolve().parents[1] / "config"
         / "puffpai_config.example.json").read_text(encoding="utf-8"))
    block = example.get("egress_doors", {})
    for door in egress_doors.DOORS:
        assert door.config_flag not in block, (
            f"the example ships an explicit egress_doors.{door.config_flag}, "
            f"which outranks the manifest and re-creates W1-e")
    assert block.get("_note"), "the block still has to tell a reader it exists"


def test_persist_posture_writes_the_chosen_door_into_the_config(box) -> None:
    """The wizard's answer has to reach the file the runtime reads."""
    _cfg, cfg_dir = box(run_egress=None)
    posture = setup_ops.assess_posture("online", "enumerated", ["weather"])
    setup_ops.persist_posture(posture)

    written = json.loads(
        (cfg_dir / "puffpai_config.json").read_text(encoding="utf-8"))
    assert written["egress_doors"]["weather"] is True
    # Doors the owner did NOT open are written explicitly false, not omitted:
    # a table listing only what is open cannot be read as a statement about
    # what is shut.
    assert written["egress_doors"]["air_quality"] is False
    assert written["egress_doors"]["news_rss"] is False


def test_a_wizard_built_config_actually_opens_the_door(box, monkeypatch) -> None:
    """End to end: choose the door, and /dashboard/weather reaches it.

    The fatal-socket harness is inverted here -- ``requests.request`` must fire
    exactly once. Before the fix the manifest said the door was open and this
    call never happened.
    """
    import requests
    from puffpai import dashboard_widgets

    box(run_egress="enumerated", doors=["weather"])
    setup_ops.persist_posture(
        setup_ops.assess_posture("online", "enumerated", ["weather"]))

    seen: list[str] = []

    class _Resp:
        status_code = 200
        headers: dict = {}
        raw = None

        def json(self):
            return {"daily": {"temperature_2m_max": [80.0],
                              "temperature_2m_min": [60.0],
                              "precipitation_sum": [0.0]},
                    "current": {"relative_humidity_2m": 50}}

    def _fake(method, url, **kw):
        seen.append(url)
        return _Resp()

    monkeypatch.setattr(egress_doors, "_send", _fake)
    monkeypatch.setattr(requests, "get", lambda *a, **k: pytest.fail("ungated"))
    dashboard_widgets._env_cache.clear()

    out = dashboard_widgets.fetch_environment(1.0, 2.0)
    assert len(seen) == 1 and "api.open-meteo.com" in seen[0]
    assert out["doors"]["weather"]["open"] is True


def test_a_wizard_run_that_did_not_choose_the_door_stays_shut(box, monkeypatch) -> None:
    """The other half: not choosing it must still mean shut."""
    import requests
    from puffpai import dashboard_widgets

    box(run_egress="enumerated", doors=[])
    setup_ops.persist_posture(
        setup_ops.assess_posture("online", "enumerated", []))

    monkeypatch.setattr(egress_doors, "_send",
                        lambda *a, **k: pytest.fail("a socket opened"))
    monkeypatch.setattr(requests, "get",
                        lambda *a, **k: pytest.fail("a socket opened"))
    dashboard_widgets._env_cache.clear()

    out = dashboard_widgets.fetch_environment(1.0, 2.0)
    assert out["doors"]["weather"]["open"] is False


def test_persist_posture_under_island_opens_nothing(box, monkeypatch) -> None:
    """Doors chosen under a non-enumerated tier stay shut: posture beats switch."""
    import requests
    from puffpai import dashboard_widgets

    box(run_egress="island")
    setup_ops.persist_posture(
        setup_ops.assess_posture("online", "island", ["weather"]))
    monkeypatch.setattr(egress_doors, "_send",
                        lambda *a, **k: pytest.fail("a socket opened"))
    dashboard_widgets._env_cache.clear()
    assert dashboard_widgets.fetch_environment(1.0, 2.0)["doors"]["weather"]["open"] is False


# ══ LAN-e ═══════════════════════════════════════════════════════════════════

def test_a_first_run_with_no_manifest_defaults_to_sealed(box) -> None:
    """"Nothing known" must mean the most restrictive rule, not the usual one."""
    _cfg, cfg_dir = box(run_egress=None)
    assert lan_posture.posture_block_from_config_dir(str(cfg_dir)) is None
    assert lan_posture.write_path_posture(str(cfg_dir)) == "sealed"


def test_first_run_sealed_refuses_a_lan_ollama_url(box) -> None:
    """The exact refuted case: --yes --run-egress sealed --ollama-url <LAN>.

    No manifest exists yet, so the write path used to read `island` and accept
    a LAN address on a box whose owner had just answered `sealed`.
    """
    box(run_egress=None)
    lan_posture.set_active_posture({"run_egress": "sealed"})
    with pytest.raises(lan_posture.PostureViolation) as exc:
        setup_ops.set_ollama_config("qwen2.5:7b-instruct",
                                    "http://192.168.1.50:11434")
    assert "'sealed'" in exc.value.user_message
    assert "island" in exc.value.user_message


def test_first_run_sealed_refuses_even_with_no_active_posture(box) -> None:
    """Belt and braces: with nothing declared and no manifest, sealed applies."""
    box(run_egress=None)
    with pytest.raises(lan_posture.PostureViolation):
        setup_ops.set_ollama_config("qwen2.5:7b-instruct",
                                    "http://192.168.1.50:11434")


def test_the_run_answer_beats_the_manifest_on_disk(box) -> None:
    """A rebuild that lowers the posture takes effect in the same run."""
    box(run_egress="island")
    assert lan_posture.write_path_posture() == "island"
    lan_posture.set_active_posture({"run_egress": "sealed"})
    assert lan_posture.write_path_posture() == "sealed"
    with pytest.raises(lan_posture.PostureViolation):
        setup_ops.write_main_config({"n8n": {"url": "http://192.168.1.9:5678"}})


def test_a_first_run_sealed_still_allows_loopback(box) -> None:
    box(run_egress=None)
    lan_posture.set_active_posture({"run_egress": "sealed"})
    setup_ops.set_ollama_config("qwen2.5:7b-instruct", "http://127.0.0.1:11434")


def test_persist_posture_declares_the_run_posture(box) -> None:
    """The wizard step wires it: after Step 1 the write path knows the answer."""
    box(run_egress=None)
    setup_ops.persist_posture(
        setup_ops.assess_posture("online", "sealed", []))
    assert lan_posture.active_posture() == "sealed"


def test_a_junk_active_posture_is_normalised_not_honoured(box) -> None:
    box(run_egress=None)
    lan_posture.set_active_posture({"run_egress": "wide-open"})
    assert lan_posture.active_posture() == build_posture.DEFAULT_RUN_EGRESS


# ══ LAN-c ═══════════════════════════════════════════════════════════════════

def test_lan_services_is_not_ok_while_the_config_file_still_holds_a_bad_url(box) -> None:
    """Neutralising fixes the process, not the file. Saying "ok" hides that."""
    cfg, _dir = box(run_egress="island",
                    main={"ollama": {"url": "https://api.example.com"},
                          "kiwix": {"url": "https://kiwix.example.com"}})
    block = lan_posture.posture_check(cfg, resolve=False)

    assert block["lan_services"] != "ok"
    services = {entry["service"] for entry in block["lan_services"]}
    assert {"ollama", "kiwix"} <= services
    assert all(e["state"] == "neutralised_at_load" for e in block["lan_services"])
    assert len(block["neutralised"]) == 2
    assert block["live_violations"] == []


def test_a_live_violation_is_reported_as_live(box) -> None:
    cfg, _dir = box(run_egress="island")
    cfg.ollama.url = "https://api.example.com"  # edited after load
    block = lan_posture.posture_check(cfg, resolve=False)
    live = [e for e in block["lan_services"] if e["state"] == "live"]
    assert [e["service"] for e in live] == ["ollama"]


def test_lan_services_is_ok_only_when_truly_clean(box) -> None:
    cfg, _dir = box(run_egress="island",
                    main={"ollama": {"url": "http://192.168.1.7:11434"}})
    block = lan_posture.posture_check(cfg, resolve=False)
    assert block["lan_services"] == "ok"
    assert block["neutralised"] == []
    assert block["live_violations"] == []


def test_status_route_carries_the_honest_block(box, monkeypatch) -> None:
    from puffpai import api_server

    box(run_egress="island", main={"ollama": {"url": "https://api.example.com"}})
    api_server.app.config["TESTING"] = True
    client = api_server.app.test_client()
    client.environ_base["HTTP_X_API_KEY"] = api_server._API_KEY
    block = client.get("/system/status").get_json()["posture_check"]
    assert "live_violations" in block and "neutralised" in block


# ══ NEW-6 ═══════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("addr", [
    "192.0.2.5",      # RFC 5737 TEST-NET-1
    "198.51.100.5",   # RFC 5737 TEST-NET-2
    "203.0.113.5",    # RFC 5737 TEST-NET-3
    "198.18.0.1",     # RFC 2544 benchmarking
    "198.19.255.254",
    "2001:db8::1",    # RFC 3849 documentation
])
def test_documentation_ranges_are_public_not_private(addr: str) -> None:
    """`ipaddress` calls these private; nothing in a house answers on them.

    Allowing one under `island` would be allowing an address that is neither
    yours nor reachable -- the permissive direction, which is the one that
    matters.
    """
    assert lan_posture.classify_host(addr) == lan_posture.PUBLIC
    literal = f"[{addr}]" if ":" in addr else addr
    assert lan_posture.check_url("ollama", f"http://{literal}:11434",
                                 "island") is not None


@pytest.mark.parametrize("addr", ["192.168.1.5", "10.1.2.3", "172.20.0.1",
                                  "169.254.1.1", "fd00::5"])
def test_real_home_ranges_are_still_private(addr: str) -> None:
    """The carve-out must not take the actual home network with it."""
    assert lan_posture.classify_host(addr) == lan_posture.PRIVATE
    literal = f"[{addr}]" if ":" in addr else addr
    assert lan_posture.check_url("ollama", f"http://{literal}:11434",
                                 "island") is None


def test_documentation_ranges_resolve_as_public_too() -> None:
    """The resolving path uses the same classifier, so it inherits the fix."""
    kind, _ = lan_posture.classify_host_resolving(
        "nas.local", resolver=lambda h, t: ["203.0.113.9"])
    assert kind == lan_posture.PUBLIC
