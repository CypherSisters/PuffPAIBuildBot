"""LAN posture — the `island` half of the egress contract, tested.

The residual W1 left open: egress doors closed the public internet, but nothing
checked the addresses *in the config*. `ollama.url = "https://someone-elses-
box.example.com"` under an `island` build was unrestricted egress that no door
saw, carrying every prompt in the household; `ha.url` the same, with a
long-lived Home Assistant token attached.

Matrix below: each posture x each URL class, at each of the three surfaces
(config load, the wizard write path, /system/status). Plus the DNS-rebinding
defence on the one dynamic-host door.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from puffpai import build_posture, config as config_mod
from puffpai.security import egress_doors, lan_posture

REPO_ROOT = Path(__file__).resolve().parents[1]


# ── the URL classes, named once ──────────────────────────────────────────────

LOOPBACK_URLS = [
    "http://localhost:11434",
    "http://127.0.0.1:8123",
    "http://[::1]:5678",
    "http://0.0.0.0:8096",
    "http://[::ffff:127.0.0.1]:8083",
]
PRIVATE_URLS = [
    "http://192.168.1.50:8123",
    "http://10.0.0.5:11434",
    "http://172.16.4.4:5678",
    "http://[fd00::1]:8096",
    "http://169.254.10.10:8083",
]
LAN_NAME_URLS = [
    "http://homeassistant.local:8123",
    "http://nas.lan:8096",
    "http://ollama.home.arpa:11434",
    "http://jellyfin:8096",
    "http://box.internal:5678",
]
PUBLIC_URLS = [
    "https://api.example.com",
    "http://8.8.8.8:11434",
    "https://ollama.somecloud.example.org:443",
    "http://[2001:4860:4860::8888]:8123",
]


@pytest.fixture(autouse=True)
def _clean_caches():
    # The run-posture override is process-global: a test file that declares one
    # and does not clear it makes the NEXT file's write-path tests read the
    # wrong posture. That is an order-dependent pass, which is worse than a
    # failure because it looks like coverage.
    lan_posture.clear_active_posture()
    lan_posture.record_load_violations([])
    egress_doors.reset_cache()
    yield
    lan_posture.clear_active_posture()
    lan_posture.record_load_violations([])
    egress_doors.reset_cache()


# ── classification ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("url", LOOPBACK_URLS)
def test_loopback_urls_classify_as_loopback(url: str) -> None:
    assert lan_posture.classify_host(lan_posture.host_of(url)) == lan_posture.LOOPBACK


@pytest.mark.parametrize("url", PRIVATE_URLS)
def test_private_urls_classify_as_private(url: str) -> None:
    assert lan_posture.classify_host(lan_posture.host_of(url)) == lan_posture.PRIVATE


@pytest.mark.parametrize("url", LAN_NAME_URLS)
def test_house_names_classify_as_lan_presumed(url: str) -> None:
    assert lan_posture.classify_host(lan_posture.host_of(url)) == lan_posture.LAN_PRESUMED


@pytest.mark.parametrize("url", PUBLIC_URLS)
def test_public_urls_classify_as_public(url: str) -> None:
    assert lan_posture.classify_host(lan_posture.host_of(url)) == lan_posture.PUBLIC


def test_classification_never_opens_a_socket(monkeypatch) -> None:
    """The load-time check must not touch DNS: startup cannot hang on a resolver."""
    import socket

    def _boom(*a, **k):
        raise AssertionError("classify_host resolved a name")

    monkeypatch.setattr(socket, "getaddrinfo", _boom)
    for url in LOOPBACK_URLS + PRIVATE_URLS + LAN_NAME_URLS + PUBLIC_URLS:
        lan_posture.classify_host(lan_posture.host_of(url))


# ── the matrix: posture x URL class ──────────────────────────────────────────

@pytest.mark.parametrize("posture", ["sealed", "island", "enumerated"])
@pytest.mark.parametrize("url", LOOPBACK_URLS)
def test_loopback_is_allowed_at_every_posture(posture: str, url: str) -> None:
    assert lan_posture.check_url("ollama", url, posture) is None


@pytest.mark.parametrize("posture", ["island", "enumerated"])
@pytest.mark.parametrize("url", PRIVATE_URLS + LAN_NAME_URLS)
def test_home_network_is_allowed_under_island_and_enumerated(posture, url) -> None:
    assert lan_posture.check_url("home_assistant", url, posture) is None


@pytest.mark.parametrize("url", PRIVATE_URLS + LAN_NAME_URLS)
def test_home_network_is_refused_under_sealed(url: str) -> None:
    """`sealed` means this machine, not this house. The message must say so."""
    violation = lan_posture.check_url("home_assistant", url, "sealed")
    assert violation is not None, url
    assert "'sealed'" in violation.user_message
    assert "island" in violation.user_message, (
        "the sealed refusal must name the posture that would allow it")


@pytest.mark.parametrize("posture", ["sealed", "island", "enumerated"])
@pytest.mark.parametrize("url", PUBLIC_URLS)
def test_public_is_refused_at_every_posture(posture: str, url: str) -> None:
    violation = lan_posture.check_url("ollama", url, posture)
    assert violation is not None, url
    assert violation.kind == lan_posture.PUBLIC
    assert f"'{posture}'" in violation.user_message, (
        "the refusal must name the posture it is enforcing")


@pytest.mark.parametrize("service_id", lan_posture.service_ids())
def test_every_service_is_checked_for_every_class(service_id: str) -> None:
    """No service gets a pass. Each row refuses public and allows loopback."""
    assert lan_posture.check_url(service_id, PUBLIC_URLS[0], "island") is not None
    assert lan_posture.check_url(service_id, LOOPBACK_URLS[0], "island") is None


def test_an_unknown_posture_fails_shut() -> None:
    """A posture string this build does not implement allows loopback only."""
    assert lan_posture.allowed_classifications("wide-open") == \
        lan_posture.allowed_classifications("sealed")


def test_an_empty_url_is_not_a_violation() -> None:
    """Not configured is not a breach."""
    for service_id in lan_posture.service_ids():
        assert lan_posture.check_url(service_id, "", "sealed") is None
        assert lan_posture.check_url(service_id, "   ", "sealed") is None


def test_a_url_with_no_host_is_refused() -> None:
    violation = lan_posture.check_url("ollama", "not-a-url", "island")
    assert violation is not None
    assert violation.kind == lan_posture.UNPARSEABLE


# ── the resolving check (DNS rebinding, LAN side) ────────────────────────────

def test_a_single_label_name_that_resolves_off_site_is_caught() -> None:
    """The hole the name-form check cannot see: a search domain sending
    `jellyfin` to a public address."""
    def _resolver(host, timeout):
        return ["93.184.216.34"]

    kind, detail = lan_posture.classify_host_resolving("jellyfin",
                                                       resolver=_resolver)
    assert kind == lan_posture.PUBLIC
    assert "93.184.216.34" in detail
    # name-form said LAN; resolving says otherwise, which is the point
    assert lan_posture.classify_host("jellyfin") == lan_posture.LAN_PRESUMED


def test_the_worst_resolved_address_wins() -> None:
    """One private answer does not launder a public one: the stack picks."""
    def _resolver(host, timeout):
        return ["192.168.1.10", "93.184.216.34"]

    kind, _ = lan_posture.classify_host_resolving("nas.local", resolver=_resolver)
    assert kind == lan_posture.PUBLIC


def test_an_unresolvable_name_is_a_warning_not_a_violation() -> None:
    """A Home Assistant that is merely switched off is not a posture breach."""
    def _resolver(host, timeout):
        raise OSError("nope")

    kind, _ = lan_posture.classify_host_resolving("homeassistant.local",
                                                  resolver=_resolver)
    assert kind == lan_posture.UNRESOLVABLE
    assert lan_posture.check_url("home_assistant", "http://homeassistant.local:8123",
                                 "island", resolve=True,
                                 resolver=_resolver) is None


def test_a_literal_address_is_never_resolved() -> None:
    def _boom(host, timeout):
        raise AssertionError("resolved a literal address")

    kind, _ = lan_posture.classify_host_resolving("192.168.1.5", resolver=_boom)
    assert kind == lan_posture.PRIVATE


# ── surface 1: config load ───────────────────────────────────────────────────

@pytest.fixture
def loaded(tmp_path, monkeypatch):
    """Build a real PuffPAIConfig from files in a temp CONFIG_DIR."""
    def _make(run_egress: str = "island", main: dict | None = None,
              ha: dict | None = None, media: dict | None = None):
        cfg_dir = tmp_path / "config"
        cfg_dir.mkdir(exist_ok=True)
        posture = build_posture.posture_manifest_block(
            build_posture.assess_posture("online", run_egress, None))
        (cfg_dir / "build_manifest.json").write_text(
            json.dumps({"schema": "build_manifest/v2", "posture": posture}),
            encoding="utf-8")
        (cfg_dir / "puffpai_config.json").write_text(
            json.dumps(main or {}), encoding="utf-8")
        if ha is not None:
            (cfg_dir / "home_assistant_config.json").write_text(
                json.dumps(ha), encoding="utf-8")
        if media is not None:
            (cfg_dir / "media_server_config.json").write_text(
                json.dumps(media), encoding="utf-8")
        monkeypatch.setenv("PUFFPAI_DATA_DIR", str(tmp_path))
        # Rebuild the singleton too: write_main_config and current_posture read
        # it, and a singleton left over from another test would answer for the
        # wrong build.
        return config_mod.get_config(_force_reload=True)

    yield _make
    # Put the process back on the real config so nothing downstream inherits
    # a temp directory that is about to be deleted.
    monkeypatch.undo()
    config_mod.get_config(_force_reload=True)
    lan_posture.record_load_violations([])


def test_load_resets_a_public_ollama_url_to_loopback(loaded) -> None:
    """The worst leak in the product, neutralised without bricking the box."""
    cfg = loaded(run_egress="island",
                 main={"ollama": {"url": "https://api.example.com",
                                  "model": "qwen2.5:7b-instruct"}})
    assert cfg.ollama.url == "http://localhost:11434"
    violations = lan_posture.load_time_violations()
    assert [v.service for v in violations] == ["ollama"]
    assert "reset to" in violations[0].detail


def test_load_disables_a_public_home_assistant(loaded) -> None:
    cfg = loaded(run_egress="island",
                 ha={"url": "https://ha.example.com", "token": "x",
                     "enabled": True})
    assert cfg.ha.enabled is False
    assert any(v.service == "home_assistant"
               for v in lan_posture.load_time_violations())


def test_load_disables_public_media_servers(loaded) -> None:
    cfg = loaded(run_egress="island",
                 media={"enabled": True,
                        "jellyfin_url": "https://jf.example.com",
                        "calibre_web_url": "http://192.168.1.9:8083"})
    assert cfg.media_server.enabled is False


def test_load_disables_a_public_n8n(loaded) -> None:
    cfg = loaded(run_egress="island",
                 main={"n8n": {"enabled": True, "url": "https://n8n.example.com"}})
    assert cfg.n8n.enabled is False


def test_sealed_disables_a_lan_home_assistant(loaded) -> None:
    """`sealed` allows this machine only, including for HA."""
    cfg = loaded(run_egress="sealed",
                 ha={"url": "http://192.168.1.50:8123", "token": "x",
                     "enabled": True})
    assert cfg.ha.enabled is False
    violation = next(v for v in lan_posture.load_time_violations()
                     if v.service == "home_assistant")
    assert "island" in violation.user_message


def test_a_clean_island_config_is_left_alone(loaded) -> None:
    cfg = loaded(run_egress="island",
                 main={"ollama": {"url": "http://192.168.1.7:11434"},
                       "n8n": {"enabled": True, "url": "http://nas.local:5678"}},
                 ha={"url": "http://homeassistant.local:8123", "token": "x",
                     "enabled": True})
    assert cfg.ollama.url == "http://192.168.1.7:11434"
    assert cfg.ha.enabled is True
    assert cfg.n8n.enabled is True
    assert lan_posture.load_time_violations() == []


def test_a_clean_sealed_config_is_left_alone(loaded) -> None:
    cfg = loaded(run_egress="sealed",
                 main={"ollama": {"url": "http://127.0.0.1:11434"}})
    assert cfg.ollama.url == "http://127.0.0.1:11434"
    assert lan_posture.load_time_violations() == []


def test_a_disabled_service_is_not_reported(loaded) -> None:
    """`ha.url` ships as a .local name, which violates `sealed`. On a box where
    Home Assistant was never set up, reporting that buries the real findings."""
    cfg = loaded(run_egress="sealed")
    assert cfg.ha.enabled is False
    assert cfg.ha.url == "http://homeassistant.local:8123"  # untouched default
    assert lan_posture.load_time_violations() == []


def test_config_load_never_raises_on_a_junk_url(loaded) -> None:
    """A bad string in a config file must not stop the appliance booting."""
    cfg = loaded(run_egress="island", main={"ollama": {"url": "::::"}})
    assert cfg is not None


def test_config_load_does_no_dns(loaded, monkeypatch) -> None:
    import socket

    def _boom(*a, **k):
        raise AssertionError("config load resolved a name")

    monkeypatch.setattr(socket, "getaddrinfo", _boom)
    loaded(run_egress="island",
           main={"ollama": {"url": "http://ollama.local:11434"}})


# ── surface 2: the wizard write path ─────────────────────────────────────────

@pytest.fixture
def offline_dns(monkeypatch):
    """Make every real lookup fail, so the writer tests are hermetic.

    This is not only test hygiene: it pins the behaviour that matters most.
    `require_allowed` resolves by default, and a resolver that is down must not
    become a way to write a URL the posture forbids — the name-form verdict has
    to survive an NXDOMAIN.
    """
    import socket

    def _nx(*a, **k):
        raise OSError("hermetic test: no DNS")

    monkeypatch.setattr(socket, "getaddrinfo", _nx)


@pytest.fixture
def public_dns(monkeypatch):
    """Every name resolves to one public address."""
    import socket

    monkeypatch.setattr(
        socket, "getaddrinfo",
        lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 443))])


def test_the_writer_refuses_a_public_url_when_it_resolves(loaded, public_dns) -> None:
    from puffpai import setup_ops

    loaded(run_egress="island")
    with pytest.raises(lan_posture.PostureViolation) as exc:
        setup_ops.write_main_config({"ollama": {"url": "https://api.example.com"}})
    assert "93.184.216.34" in exc.value.detail


def test_the_writer_refuses_a_lan_name_that_resolves_off_site(loaded,
                                                              public_dns) -> None:
    """A `.local` name is only allowed if it actually points inside the house."""
    from puffpai import setup_ops

    loaded(run_egress="island")
    with pytest.raises(lan_posture.PostureViolation):
        setup_ops.write_main_config({"n8n": {"url": "http://nas.local:5678"}})


def test_the_writer_refuses_a_public_url(loaded, offline_dns, monkeypatch) -> None:
    """A violating URL is never persisted; the wizard says why."""
    from puffpai import setup_ops

    loaded(run_egress="island")
    with pytest.raises(lan_posture.PostureViolation) as exc:
        setup_ops.write_main_config({"ollama": {"url": "https://api.example.com"}})
    assert "'island'" in exc.value.user_message
    assert "ollama.url" in exc.value.user_message


def test_the_writer_refuses_an_ollama_url_from_the_cli_flag(loaded, offline_dns) -> None:
    """`--ollama-url` lands here via set_ollama_config, so it is covered."""
    from puffpai import setup_ops

    loaded(run_egress="island")
    with pytest.raises(lan_posture.PostureViolation):
        setup_ops.set_ollama_config("qwen2.5:7b-instruct",
                                    "https://ollama.example.com")


def test_the_writer_allows_a_lan_url(loaded, offline_dns, tmp_path) -> None:
    from puffpai import setup_ops

    loaded(run_egress="island")
    path = setup_ops.write_main_config({"ollama": {"url": "http://192.168.1.7:11434"}})
    assert json.loads(Path(path).read_text(encoding="utf-8"))["ollama"]["url"] \
        == "http://192.168.1.7:11434"


def test_the_writer_refuses_a_lan_url_under_sealed(loaded, offline_dns) -> None:
    from puffpai import setup_ops

    loaded(run_egress="sealed")
    with pytest.raises(lan_posture.PostureViolation):
        setup_ops.write_main_config({"n8n": {"url": "http://192.168.1.7:5678"}})


# ── surface 3: /system/status ────────────────────────────────────────────────

def test_posture_check_says_ok_on_a_clean_build(loaded) -> None:
    cfg = loaded(run_egress="island",
                 main={"ollama": {"url": "http://127.0.0.1:11434"}})
    block = lan_posture.posture_check(cfg, resolve=False)
    assert block["posture"] == "island"
    assert block["lan_services"] == "ok"


def test_posture_check_lists_violations(loaded) -> None:
    cfg = loaded(run_egress="island")
    cfg.ollama.url = "https://api.example.com"   # as if edited under us
    block = lan_posture.posture_check(cfg, resolve=False)
    assert isinstance(block["lan_services"], list)
    assert block["lan_services"][0]["service"] == "ollama"
    assert "'island'" in block["lan_services"][0]["message"]


def test_posture_check_reports_what_load_neutralised(loaded) -> None:
    """A neutralisation must be visible, not silent."""
    loaded(run_egress="island",
           main={"ollama": {"url": "https://api.example.com"}})
    from puffpai.config import get_config
    block = lan_posture.posture_check(get_config(), resolve=False)
    assert block["neutralised_at_load"][0]["service"] == "ollama"


def test_status_route_publishes_posture_check(loaded, monkeypatch) -> None:
    from puffpai import api_server

    loaded(run_egress="island")
    api_server.app.config["TESTING"] = True
    client = api_server.app.test_client()
    client.environ_base["HTTP_X_API_KEY"] = api_server._API_KEY
    body = client.get("/system/status").get_json()

    assert "posture_check" in body
    assert body["posture_check"]["lan_services"] in ("ok", "unknown") or \
        isinstance(body["posture_check"]["lan_services"], list)
    assert "checked" in body["posture_check"]


# ── the table must cover config.py ───────────────────────────────────────────

def test_every_url_field_in_config_is_covered() -> None:
    """A new LAN service URL added to config.py must join the SERVICES table.

    Without this, the next `foo.url` field ships unchecked and the island
    promise quietly becomes false again in exactly the way it did before.
    """
    text = (REPO_ROOT / "src" / "puffpai" / "config.py").read_text(encoding="utf-8")
    fields = set(re.findall(r"^\s+([a-z_]*url[a-z_]*)\s*:\s*str", text, re.M))
    covered = {s.config_path.split(".", 1)[1] for s in lan_posture.SERVICES}
    assert fields <= covered, (
        f"URL fields in config.py with no lan_posture.SERVICES row: "
        f"{sorted(fields - covered)}")


# ── DNS rebinding on the dynamic-host egress door ────────────────────────────

@pytest.fixture
def enumerated_news(tmp_path, monkeypatch):
    """An enumerated build with the news_rss door open."""
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir(exist_ok=True)
    posture = build_posture.posture_manifest_block(
        build_posture.assess_posture("online", "enumerated", None))
    (cfg_dir / "build_manifest.json").write_text(
        json.dumps({"schema": "build_manifest/v2", "posture": posture}),
        encoding="utf-8")
    (cfg_dir / "puffpai_config.json").write_text(
        json.dumps({"egress_doors": {"news_rss": True}}), encoding="utf-8")

    class _Cfg:
        CONFIG_DIR = str(cfg_dir)
        REPO_ROOT = str(cfg_dir)
        DATA_DIR = str(cfg_dir)

    monkeypatch.setattr("puffpai.config.get_config", lambda **kw: _Cfg())
    for d in egress_doors.DOORS:
        monkeypatch.delenv(d.env_var, raising=False)
    egress_doors.reset_cache()
    return _Cfg


def test_a_name_resolving_to_a_private_address_is_refused(enumerated_news,
                                                          monkeypatch) -> None:
    """The rebinding case the literal-IP check could not see."""
    import requests

    def _fatal(*a, **k):
        raise AssertionError("a socket opened for a name pointing inward")

    monkeypatch.setattr(egress_doors, "_send", _fatal)

    with pytest.raises(egress_doors.EgressBlocked) as exc:
        egress_doors.egress_get("news_rss", "https://feeds.evil.test/rss",
                                resolver=lambda h, p: ["169.254.169.254"])
    assert "169.254.169.254" in exc.value.reason
    assert "link-local" in exc.value.reason


@pytest.mark.parametrize("addr", ["127.0.0.1", "10.0.0.1", "192.168.1.1",
                                  "::1", "0.0.0.0", "::ffff:127.0.0.1"])
def test_every_internal_answer_is_refused(enumerated_news, monkeypatch, addr) -> None:
    import requests
    monkeypatch.setattr(egress_doors, "_send",
                        lambda *a, **k: pytest.fail("socket opened"))
    with pytest.raises(egress_doors.EgressBlocked):
        egress_doors.egress_get("news_rss", "https://feeds.example.test/rss",
                                resolver=lambda h, p: [addr])


def test_one_bad_answer_among_good_ones_is_refused(enumerated_news,
                                                   monkeypatch) -> None:
    """Every answer must pass: the stack picks which one the socket gets."""
    import requests
    monkeypatch.setattr(egress_doors, "_send",
                        lambda *a, **k: pytest.fail("socket opened"))
    with pytest.raises(egress_doors.EgressBlocked):
        egress_doors.egress_get(
            "news_rss", "https://feeds.example.test/rss",
            resolver=lambda h, p: ["93.184.216.34", "127.0.0.1"])


def test_an_unresolvable_feed_is_refused_without_a_socket(enumerated_news,
                                                          monkeypatch) -> None:
    import requests
    monkeypatch.setattr(egress_doors, "_send",
                        lambda *a, **k: pytest.fail("socket opened"))

    def _no(host, port):
        raise OSError("nxdomain")

    with pytest.raises(egress_doors.EgressBlocked):
        egress_doors.egress_get("news_rss", "https://feeds.example.test/rss",
                                resolver=_no)


def test_a_public_feed_is_allowed(enumerated_news, monkeypatch) -> None:
    import requests

    class _Resp:
        status_code = 200
        headers: dict = {}
        content = b"<rss></rss>"
        raw = None

        def raise_for_status(self):
            return None

    monkeypatch.setattr(egress_doors, "_send", lambda *a, **k: _Resp())
    resp = egress_doors.egress_get("news_rss", "https://feeds.example.test/rss",
                                   resolver=lambda h, p: ["93.184.216.34"])
    assert resp.status_code == 200


def test_the_peer_is_re_checked_after_connecting(enumerated_news,
                                                 monkeypatch) -> None:
    """Closes the window between the lookup and the connect.

    The resolver answers with a public address; the socket lands on a private
    one. That is a rebinding attack succeeding against the pre-flight check,
    and the reply must be discarded rather than handed back.
    """
    import requests

    class _Sock:
        @staticmethod
        def getpeername():
            return ("192.168.1.50", 443)

    class _Conn:
        sock = _Sock()

    class _Raw:
        _connection = _Conn()

    class _Resp:
        status_code = 200
        headers: dict = {}
        raw = _Raw()

    monkeypatch.setattr(egress_doors, "_send", lambda *a, **k: _Resp())
    with pytest.raises(egress_doors.EgressBlocked) as exc:
        egress_doors.egress_get("news_rss", "https://feeds.example.test/rss",
                                resolver=lambda h, p: ["93.184.216.34"])
    assert "192.168.1.50" in exc.value.reason


def test_a_redirect_target_is_resolved_and_checked_too(enumerated_news,
                                                       monkeypatch) -> None:
    """The approval that covered hop one says nothing about hop two."""
    import requests

    class _Redirect:
        status_code = 302
        headers = {"Location": "https://feeds.example.test/inner"}
        raw = None

    calls: list[str] = []

    def _resolver(host, port):
        calls.append(host)
        return ["93.184.216.34"] if len(calls) == 1 else ["127.0.0.1"]

    monkeypatch.setattr(egress_doors, "_send", lambda *a, **k: _Redirect())
    with pytest.raises(egress_doors.EgressBlocked):
        egress_doors.egress_get("news_rss", "https://feeds.example.test/rss",
                                resolver=_resolver)
    assert len(calls) == 2, "the redirect hop was not re-resolved"


def test_fixed_host_doors_are_not_resolved(enumerated_news, monkeypatch) -> None:
    """A reviewed host allowlist needs no resolver; do not add a DNS dependency."""
    import requests

    class _Resp:
        status_code = 200
        headers: dict = {}
        raw = None

        def json(self):
            return {}

    monkeypatch.setattr(egress_doors, "_send", lambda *a, **k: _Resp())
    monkeypatch.setenv("PUFFPAI_EGRESS_WEATHER", "on")
    egress_doors.reset_cache()

    def _boom(host, port):
        raise AssertionError("a fixed-host door resolved its own host")

    resp = egress_doors.egress_get("weather",
                                   "https://api.open-meteo.com/v1/forecast",
                                   resolver=_boom)
    assert resp.status_code == 200
