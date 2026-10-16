"""The dashboard weather route must obey the build's egress posture.

Without this, ``GET /dashboard/weather`` calls ``api.open-meteo.com`` and
``air-quality-api.open-meteo.com`` unconditionally, the dashboard polls it on
mount and every ten minutes, and the egress log records ``"enforced": false``,
on a build whose README and SECURITY.md tell
a stranger to point a deny-by-default firewall at it and confirm nothing goes
out beyond Ollama and Home Assistant.

The tests below are deliberately written so that the *absence of a socket* is
what passes, not the presence of a nice error message: ``requests.get`` and
``requests.request`` are replaced with functions that fail the test if they are
ever reached. A gate that produces the right JSON after connecting would still
be a broken gate.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

from puffpai import build_manifest, build_posture, dashboard_widgets
from puffpai.security import egress_doors


# ── harness ──────────────────────────────────────────────────────────────────

class _Cfg:
    """Minimal stand-in for PuffPAIConfig: the doors only read CONFIG_DIR."""

    def __init__(self, config_dir: str):
        self.CONFIG_DIR = config_dir
        self.REPO_ROOT = config_dir
        self.DATA_DIR = config_dir


@pytest.fixture
def build(tmp_path, monkeypatch):
    """Write a manifest + config into a temp CONFIG_DIR and point everything there.

    Returns a callable ``build(run_egress=..., doors={...}, switches={...})``.
    Nothing here monkeypatches the door module's own logic — only the two files
    it reads — so the real posture normalisation and flag resolution run.
    """
    def _make(run_egress: str = "island",
              doors: dict | None = None,
              switches: dict | None = None) -> Path:
        cfg_dir = tmp_path / "config"
        cfg_dir.mkdir(exist_ok=True)
        posture = build_posture.posture_manifest_block(
            build_posture.assess_posture(
                "online", run_egress, sorted(switches or {})))
        (cfg_dir / "build_manifest.json").write_text(
            json.dumps({"schema": build_manifest.SCHEMA, "posture": posture}),
            encoding="utf-8")
        (cfg_dir / "puffpai_config.json").write_text(
            json.dumps({"egress_doors": doors or {}}), encoding="utf-8")

        monkeypatch.setattr("puffpai.config.get_config",
                            lambda **kw: _Cfg(str(cfg_dir)))
        egress_doors.reset_cache()
        return cfg_dir

    yield _make
    egress_doors.reset_cache()


@pytest.fixture(autouse=True)
def _no_stray_env(monkeypatch):
    """A door env override on the developer's machine must not decide a test."""
    for d in egress_doors.DOORS:
        monkeypatch.delenv(d.env_var, raising=False)


@pytest.fixture
def no_sockets(monkeypatch):
    """Make every outbound entry point in ``requests`` fatal.

    This is the actual assertion for the sealed/island cases: if the gate is
    ever evaluated *after* a connection, the test fails inside requests rather
    than at an assert, which is the honest place for it to fail.
    """
    import requests

    calls: list[str] = []

    def _boom(*args, **kwargs):
        target = args[0] if args else kwargs.get("url", "?")
        raise AssertionError(
            f"a socket was opened while every egress door was shut: {target}")

    monkeypatch.setattr(requests, "get", _boom)
    monkeypatch.setattr(requests, "post", _boom)
    monkeypatch.setattr(egress_doors, "_send", _boom)
    return calls


class _Resp:
    status_code = 200
    headers: dict = {}

    def __init__(self, payload: dict):
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None


@pytest.fixture
def counted_requests(monkeypatch):
    """Replace ``requests.request`` with a counter that never leaves the box."""
    import requests

    seen: list[tuple[str, str]] = []

    _FORECAST = {"daily": {"temperature_2m_max": [81.0],
                           "temperature_2m_min": [64.0],
                           "precipitation_sum": [0.0]},
                 "current": {"relative_humidity_2m": 55}}
    _AIR = {"current": {"us_aqi": 42, "pm2_5": 6.0, "pm10": 14.0}}

    def _fake(method, url, **kwargs):
        seen.append((method, url))
        return _Resp(_AIR if "air-quality" in url else _FORECAST)

    monkeypatch.setattr(egress_doors, "_send", _fake)

    def _fatal_direct(*args, **kwargs):
        raise AssertionError(
            "an outbound call bypassed egress_doors.egress_request")

    monkeypatch.setattr(requests, "get", _fatal_direct)
    monkeypatch.setattr(requests, "post", _fatal_direct)
    return seen


@pytest.fixture(autouse=True)
def _clear_caches():
    dashboard_widgets._env_cache.clear()
    dashboard_widgets._news_cache.clear()
    yield
    dashboard_widgets._env_cache.clear()
    dashboard_widgets._news_cache.clear()


# ── the registry's own invariants ────────────────────────────────────────────

def test_every_door_defaults_shut() -> None:
    """The contract, as a value rather than a promise in a comment."""
    for d in egress_doors.DOORS:
        assert d.default is False, f"door {d.id} ships open"


def test_a_door_without_hosts_is_marked_dynamic() -> None:
    for d in egress_doors.DOORS:
        assert bool(d.hosts) or d.dynamic_hosts, (
            f"door {d.id} declares no hosts and is not marked dynamic")


def test_no_manifest_and_no_config_means_every_door_shut(tmp_path, monkeypatch) -> None:
    """A box where no wizard has run reaches nothing. Fail-closed by default."""
    monkeypatch.setattr("puffpai.config.get_config",
                        lambda **kw: _Cfg(str(tmp_path)))
    egress_doors.reset_cache()
    for door_id in egress_doors.door_ids():
        allowed, reason = egress_doors.egress_allowed(door_id)
        assert allowed is False, f"{door_id} open with no manifest: {reason}"
    egress_doors.reset_cache()


def test_unreadable_config_fails_shut(build, tmp_path) -> None:
    cfg_dir = build(run_egress="enumerated", doors={"weather": True})
    (cfg_dir / "puffpai_config.json").write_text("{ not json", encoding="utf-8")
    egress_doors.reset_cache()
    allowed, reason = egress_doors.egress_allowed("weather")
    assert allowed is False, reason


def test_junk_switch_value_fails_shut(build) -> None:
    build(run_egress="enumerated", doors={"weather": "maybe"})
    allowed, reason = egress_doors.egress_allowed("weather")
    assert allowed is False
    assert "not a switch value" in reason


def test_unknown_door_is_refused(build) -> None:
    build(run_egress="enumerated")
    allowed, reason = egress_doors.egress_allowed("exfiltrate")
    assert allowed is False
    assert "no egress door" in reason


# ── posture beats every switch ───────────────────────────────────────────────

@pytest.mark.parametrize("posture", ["sealed", "island"])
def test_posture_shuts_the_door_even_with_the_flag_on(build, posture) -> None:
    """A config key must never overrule the owner's posture answer."""
    build(run_egress=posture,
          doors={"weather": True, "air_quality": True, "news_rss": True})
    for door_id in egress_doors.door_ids():
        allowed, reason = egress_doors.egress_allowed(door_id)
        assert allowed is False, f"{door_id} open under {posture}: {reason}"
        assert f"(posture: {posture})" in reason


def test_unrecognised_posture_falls_back_to_the_safe_default(build) -> None:
    """A hand-edited manifest cannot invent a permissive posture.

    ``read_manifest`` returns the file unprojected, so ``run_posture`` puts it
    through ``build_posture.assess_posture``, where an unknown value becomes the
    default (``island``) rather than being honoured.
    """
    build(run_egress="wide-open", doors={"weather": True})
    assert egress_doors.run_posture()["run_egress"] == "island"
    allowed, _ = egress_doors.egress_allowed("weather")
    assert allowed is False


# ── the four core cases ──────────────────────────────────────────────────────

def test_sealed_opens_no_socket(build, no_sockets) -> None:
    """The thesis. Under `sealed`, fetch_environment reaches the network zero times."""
    build(run_egress="sealed",
          doors={"weather": True, "air_quality": True})
    out = dashboard_widgets.fetch_environment(1.0, 2.0)
    assert out["doors"]["weather"]["open"] is False
    assert out["doors"]["air_quality"]["open"] is False
    assert "high" not in out and "us_aqi" not in out
    # And no fabricated air data stands in for the measurement that never happened.
    assert "air_breakdown" not in out


def test_island_opens_no_socket(build, no_sockets) -> None:
    build(run_egress="island", doors={"weather": True})
    out = dashboard_widgets.fetch_environment(1.0, 2.0)
    assert out["doors"]["weather"]["open"] is False


def test_enumerated_without_the_flag_is_shut(build, no_sockets) -> None:
    """The tier is not the permission. Every switch starts off."""
    build(run_egress="enumerated", doors={})
    out = dashboard_widgets.fetch_environment(1.0, 2.0)
    assert out["doors"]["weather"]["open"] is False
    reason = out["doors"]["weather"]["reason"]
    assert "posture: enumerated" in reason and "is not set" in reason


def test_enumerated_with_the_flag_makes_exactly_one_call(build, counted_requests) -> None:
    """Weather on, air quality left off: one host is contacted, not two."""
    build(run_egress="enumerated", doors={"weather": True})
    out = dashboard_widgets.fetch_environment(1.0, 2.0)

    assert len(counted_requests) == 1, counted_requests
    method, url = counted_requests[0]
    assert method == "GET"
    assert url.startswith("https://api.open-meteo.com/")
    assert out["high"] == 81 and out["low"] == 64
    assert out["doors"]["weather"]["open"] is True
    assert out["doors"]["air_quality"]["open"] is False
    assert "us_aqi" not in out


def test_both_flags_on_makes_exactly_two_calls(build, counted_requests) -> None:
    build(run_egress="enumerated",
          doors={"weather": True, "air_quality": True})
    out = dashboard_widgets.fetch_environment(1.0, 2.0)
    assert len(counted_requests) == 2, counted_requests
    assert out["us_aqi"] == 42
    assert "air_breakdown" in out


def test_the_wizards_own_weather_switch_opens_the_weather_door(build, counted_requests) -> None:
    """What the owner picked in the wizard's enumerated table is honoured.

    ...and it opens ONLY what it named: the wizard offers one 'weather' switch,
    so air_quality — a second host learning the coordinates — stays shut.
    """
    build(run_egress="enumerated", switches={"weather": True})
    out = dashboard_widgets.fetch_environment(1.0, 2.0)
    assert len(counted_requests) == 1
    assert out["doors"]["air_quality"]["open"] is False


def test_env_var_forces_a_door_shut_over_config(build, no_sockets, monkeypatch) -> None:
    """An operator can shut a door from a unit file without editing the JSON."""
    build(run_egress="enumerated", doors={"weather": True})
    monkeypatch.setenv("PUFFPAI_EGRESS_WEATHER", "off")
    egress_doors.reset_cache()
    out = dashboard_widgets.fetch_environment(1.0, 2.0)
    assert out["doors"]["weather"]["open"] is False


# ── URL rules ────────────────────────────────────────────────────────────────

def test_an_open_door_still_refuses_an_undeclared_host(build, counted_requests) -> None:
    """A door is a named destination, not a licence to fetch anything."""
    build(run_egress="enumerated", doors={"weather": True})
    with pytest.raises(egress_doors.EgressBlocked) as exc:
        egress_doors.egress_get("weather", "https://evil.example.com/v1/forecast")
    assert "not one of the addresses" in exc.value.user_message
    assert counted_requests == [], "a refused host still opened a socket"


def test_an_open_door_refuses_plain_http(build, counted_requests) -> None:
    build(run_egress="enumerated", doors={"weather": True})
    with pytest.raises(egress_doors.EgressBlocked):
        egress_doors.egress_get("weather", "http://api.open-meteo.com/v1/forecast")
    assert counted_requests == []


def test_a_dynamic_door_refuses_loopback_and_private_addresses(build, counted_requests) -> None:
    """The news door takes the URL the owner names; it still may not go inward."""
    build(run_egress="enumerated", doors={"news_rss": True})
    for host in ("127.0.0.1", "192.168.1.10", "169.254.169.254", "10.0.0.5"):
        ok, result = dashboard_widgets.fetch_news(f"https://{host}/feed.xml")
        assert ok is False, host
        assert "this machine or this network" in result, host
    assert counted_requests == []


def test_a_shut_news_door_returns_the_doors_own_sentence(build, no_sockets) -> None:
    build(run_egress="island", doors={"news_rss": True})
    ok, result = dashboard_widgets.fetch_news("https://feeds.npr.org/1001/rss.xml")
    assert ok is False
    # The door's own sentence, in plain words (no internal id).
    assert "News headlines are switched off" in result
    assert "news_rss" not in result


# ── the egress log ───────────────────────────────────────────────────────────

def test_egress_log_records_enforced_true_and_the_refusal(build, no_sockets,
                                                          tmp_path, monkeypatch) -> None:
    """``enforced: false`` was the honest old record. It is now false-to-fact."""
    log = tmp_path / "logs" / "widget_egress.jsonl"
    monkeypatch.setattr(dashboard_widgets, "_EGRESS_LOG", str(log))
    build(run_egress="sealed", doors={"weather": True})
    dashboard_widgets.fetch_environment(1.0, 2.0)

    lines = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines() if x]
    assert lines, "nothing was logged"
    for entry in lines:
        assert entry["enforced"] is True
        assert entry["sent"] is False
        assert entry["door"] in egress_doors.door_ids()
        assert "is shut" in entry["decision"]


def test_egress_log_drops_the_query_string(build, counted_requests,
                                           tmp_path, monkeypatch) -> None:
    """The coordinates ride in the query string. A security log is not the
    place to write down where the household is."""
    log = tmp_path / "logs" / "widget_egress.jsonl"
    monkeypatch.setattr(dashboard_widgets, "_EGRESS_LOG", str(log))
    build(run_egress="enumerated", doors={"weather": True})
    dashboard_widgets.fetch_environment(39.7392, -104.9903)

    text = log.read_text(encoding="utf-8")
    assert "39.7392" not in text and "104.9903" not in text
    assert "?" not in text.split('"url": "')[1].split('"')[0]


def test_a_refusal_is_not_cached(build, no_sockets) -> None:
    """Flipping a switch on must take effect now, not in thirty minutes."""
    build(run_egress="sealed", doors={"weather": True})
    dashboard_widgets.fetch_environment(1.0, 2.0)
    assert dashboard_widgets._env_cache == {}


# ── /system/status ───────────────────────────────────────────────────────────

def test_status_lists_every_door_including_the_shut_ones(build) -> None:
    """A status page that reports only what is open cannot be used to audit."""
    build(run_egress="enumerated", doors={"weather": True})
    block = egress_doors.door_states()

    assert block["posture"] == "enumerated"
    listed = {d["id"]: d for d in block["doors"]}
    assert set(listed) == set(egress_doors.door_ids())
    assert listed["weather"]["open"] is True
    assert listed["air_quality"]["open"] is False
    assert listed["news_rss"]["open"] is False
    for entry in listed.values():
        # Every door says what it sends and who stops it. No door in this
        # build is listed as enumerated-but-ungated.
        assert entry["enforced"] is True
        assert entry["data_sent"]
        # A book site's one switch is on What leaves your computer, so it
        # carries no config flag of its own (no dead switch).
        assert (entry["config_flag"] or "").startswith("egress_doors.") or (
            entry["config_flag"] is None and entry.get("switched_in"))


def test_status_says_the_posture_it_read(build) -> None:
    build(run_egress="sealed")
    block = egress_doors.door_states()
    assert block["posture"] == "sealed"
    assert block["open_posture"] == "enumerated"
    assert all(d["open"] is False for d in block["doors"])


# ── the registry is the complete list ────────────────────────────────────────

#: Modules that ARE the gate, and are exempt as a whole.
#:  - egress_doors.py is the registry itself.
#:  - source_fetch.py is the wizard's sourcing path, gated by
#:    config/source_registry.json: a human-reviewed allowlist, a licence shown
#:    before a byte moves, per-hop redirect and private-address validation and
#:    a size ceiling. Build-time, governed by the build_network axis.
#:  - pinned_net.py is the pinned-connection transport both of the above use.
#:  tests/test_outside_single_exit.py holds the wider scan (sockets too).
_GATE_MODULES = {"egress_doors.py", "source_fetch.py", "pinned_net.py"}

#: Marker a LAN call site must carry, on the same line. Whole modules used to
#: be allowlisted by name -- api_server.py among them, which is 250 KB and the
#: single likeliest place for a new outbound call to appear. A blanket pass on
#: a file that large is not a gate, so the exemption is now per call and has to
#: be typed next to the call, with a reason.
_LAN_MARKER = "# lan-egress:"

_OUTBOUND_RE = re.compile(
    r"(?:^|[^A-Za-z0-9_\.])_?requests\.(get|post|put|patch|delete|request|Session)\b"
    r"|(?:^|[^A-Za-z0-9_\.])httpx\.(get|post|put|patch|delete|request|Client|AsyncClient)\b"
    r"|urllib\.request\.(urlopen|build_opener)"
)


def _outbound_call_sites() -> list[tuple[str, int, str]]:
    src = Path(__file__).resolve().parents[1] / "src" / "puffpai"
    found = []
    for path in sorted(src.rglob("*.py")):
        if path.name in _GATE_MODULES:
            continue
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if _OUTBOUND_RE.search(line):
                found.append((str(path.relative_to(src)), i, line))
    return found


def test_every_outbound_call_site_is_gated_or_marked() -> None:
    """Adding an outbound call means routing it through a door or marking it.

    This is what keeps the enumerated-egress claim true as the code grows. A
    call goes through ``egress_doors.egress_get``, or it carries an inline
    ``# lan-egress: <reason>`` saying it is a LAN destination the owner
    configured -- which ``lan_posture`` then holds to the run posture. There is
    no third option and no module-wide pass.
    """
    offenders = [f"{rel}:{i}: {line.strip()}"
                 for rel, i, line in _outbound_call_sites()
                 if _LAN_MARKER not in line]
    assert not offenders, (
        "ungated outbound call sites -- route through "
        "egress_doors.egress_get, or mark the line "
        "'# lan-egress: <why this is a LAN destination>':\n  "
        + "\n  ".join(offenders))


def test_the_call_site_guard_is_not_vacuous() -> None:
    """The scan must actually be finding call sites, and api_server among them."""
    sites = _outbound_call_sites()
    assert len(sites) >= 12, f"scan found only {len(sites)} outbound call sites"
    assert any(rel == "api_server.py" for rel, _i, _l in sites), (
        "api_server.py is no longer being scanned -- it is the largest module "
        "and the likeliest place for an ungated call to appear")


def test_an_aliased_import_does_not_evade_the_guard() -> None:
    """api_server uses `import requests as _requests`, which the first version
    of this pattern did not match. The alias must not be a way out."""
    assert _OUTBOUND_RE.search("        r = _requests.get(url)")
    assert _OUTBOUND_RE.search("resp = requests.post(url)")
    assert _OUTBOUND_RE.search("httpx.Client()")
    assert _OUTBOUND_RE.search("urllib.request.urlopen(url)")
    # ...and the marker is what exempts it, not the module it lives in
    assert _LAN_MARKER in "r = _requests.get(u)  # lan-egress: Ollama, LAN"


def test_every_marked_call_gives_a_reason() -> None:
    """`# lan-egress:` with nothing after it is a bypass, not a justification."""
    thin = [f"{rel}:{i}" for rel, i, line in _outbound_call_sites()
            if _LAN_MARKER in line
            and len(line.split(_LAN_MARKER, 1)[1].split()) < 3]
    assert not thin, f"lan-egress markers with no real reason: {thin}"


def test_dashboard_widgets_makes_no_direct_requests_call() -> None:
    """The module W1 was filed against, checked specifically."""
    text = (Path(__file__).resolve().parents[1] / "src" / "puffpai"
            / "dashboard_widgets.py").read_text(encoding="utf-8")
    assert "requests.get(" not in text
    assert "import requests" not in text
