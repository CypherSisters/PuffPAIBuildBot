"""The Bridge is real.

"Drawbridge up" means the bot reaches nothing on the home network or the
internet except the services the owner switched on, Home Assistant included
only when it is switched on. A build that keeps everything on this computer
("sealed") reaches nothing at all. These tests fail if that is bypassed:

* by an "as built" service (BuildBot's own finding and adding tools, the
  book library, the sample look), which with the Bridge up is off until it
  is switched on;
* by the locked box (its install-time parts and its run-time hosts);
* by app traffic, live cards and the built-in services (a settings-file or
  environment "yes" never opens one with the Bridge up; only the owner's own
  yes, or her answer in the wizard, does);
* by the brain or the books pointed at another computer at home;
* by any other connection at all, caught at the moment it is made
  (``security.bridge_guard`` on the ``socket.connect`` audit hook), asyncio's
  Windows loop included.

No test here sends a packet off this computer: the connect-time check is
driven through ``sys.audit`` with the event the socket module raises, the
asyncio tests swap the loop's real connect for a recorder (one test makes a
real connection to 127.0.0.1), and every fetch uses a stand-in transport that
records what it was asked.
"""
from __future__ import annotations

import json
import os
import sys
import types

import pytest

import conftest
from puffpai import outside_services, plain_words, source_fetch, zone_bridge
from puffpai.box import driver
from puffpai.config import get_config
from puffpai.security import (bridge_guard, egress_doors, egress_ledger,
                              lan_guard, lan_posture, outside_switches,
                              pinned_net)


# ── helpers ──────────────────────────────────────────────────────────────────

def _write_manifest(bridge=None, run_egress="enumerated", raw=None):
    cfg = get_config()
    path = os.path.join(cfg.CONFIG_DIR, "build_manifest.json")
    os.makedirs(cfg.CONFIG_DIR, exist_ok=True)
    if raw is not None:
        text = raw
    else:
        data = {"posture": {"build_network": "online", "run_egress": run_egress}}
        if bridge is not None:
            data["bridge"] = {"posture": bridge} if isinstance(bridge, str) else bridge
        text = json.dumps(data)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    bridge_guard.forget_enforced()
    egress_doors.reset_cache()
    outside_switches.forget()
    return path


@pytest.fixture
def bridge_up():
    _write_manifest("drawbridge_up")
    yield
    bridge_guard.forget_enforced()


@pytest.fixture
def sealed():
    _write_manifest("fenced", run_egress="sealed")
    yield
    bridge_guard.forget_enforced()


@pytest.fixture
def fenced():
    _write_manifest("fenced")
    yield
    bridge_guard.forget_enforced()


def _module_at(path: str, name: str, code: str) -> types.ModuleType:
    """A throwaway module whose frames carry *path* as their file name."""
    import linecache
    mod = types.ModuleType(name)
    if path.startswith(conftest._SRC_ROOT) and not os.path.exists(path):
        linecache.cache[path] = (len(code), None, code.splitlines(True), path)
    exec(compile(code, path, "exec"), mod.__dict__)
    return mod


_PROBE_CODE = (
    "import sys\n"
    "def marked(addr, sock=None):\n"
    "    sys.audit('socket.connect', sock, addr)  # lan-egress: a home service\n"
    "\n"
    "\n"
    "\n"
    "def unmarked(addr, sock=None):\n"
    "    sys.audit('socket.connect', sock, addr)\n")

_PROBES = _module_at(os.path.join(conftest._SRC_ROOT, "zz_dev_sec_probe.py"),
                     "zz_dev_sec_probe", _PROBE_CODE)
# The same calls, made from inside a listed exit (the frame's file is the real
# exit's path; the file itself is not read or changed).
_EXIT_PROBES = _module_at(
    os.path.join(conftest._SRC_ROOT, "security", "pinned_net.py"),
    "zz_dev_sec_exit_probe",
    "import sys\n"
    "def dial(addr, sock=None):\n"
    "    sys.audit('socket.connect', sock, addr)\n")


# ── what is in force ────────────────────────────────────────────────────────

@pytest.mark.parametrize("manifest, level, posture", [
    (None, "open", "fenced"),
    ({"posture": {"run_egress": "island"}}, "open", "fenced"),
    ({"posture": {"run_egress": "enumerated"}, "bridge": {"posture": "open_yard"}}, "open", "open_yard"),
    ({"posture": {"run_egress": "island"}, "bridge": {"posture": "open_yard"}}, "open", "fenced"),
    ({"posture": {"run_egress": "island"}, "bridge": {"posture": "moat"}}, "open", "fenced"),
    ({"posture": {"run_egress": "island"}, "bridge": {"posture": "drawbridge_up"}}, "switched_on", "drawbridge_up"),
    ({"posture": {"run_egress": "enumerated"}, "bridge": {"posture": "DRAWBRIDGE_UP "}}, "switched_on", "drawbridge_up"),
    ({"posture": {"run_egress": "island"}, "bridge": {"posture": "wide-open"}}, "switched_on", "drawbridge_up"),
    ({"posture": {"run_egress": "island"}, "bridge": "junk"}, "switched_on", "drawbridge_up"),
    ({"posture": {"run_egress": "sealed"}, "bridge": {"posture": "open_yard"}}, "sealed", "drawbridge_up"),
    ("not a manifest", "switched_on", "drawbridge_up"),
])
def test_the_bridge_in_force_is_the_stricter_reading(manifest, level, posture):
    got = zone_bridge.enforced_from_manifest(manifest)
    assert got["level"] == level and got["posture"] == posture
    assert got["line"] == zone_bridge.ENFORCED_LINES[level]


def test_a_manifest_that_cannot_be_read_is_read_as_bridge_up():
    _write_manifest(raw="{ this is not json")
    assert bridge_guard.reach_level() == "switched_on"
    assert outside_switches.is_off("code_search")


def test_no_manifest_changes_nothing():
    bridge_guard.forget_enforced()
    assert bridge_guard.reach_level() == "open"
    assert not outside_switches.is_off("code_search")


# ── "as built" services ──────────────────────────────────────────────────────

_AS_BUILT = ("library", "catalog_refresh", "sample_look", "internet_check",
             "wikipedia_books", "code_search", "code_download",
             "picture_search", "picture_download", "app_parts")


@pytest.mark.parametrize("sid", _AS_BUILT)
def test_with_the_bridge_up_an_as_built_service_is_off_until_switched_on(bridge_up, sid):
    assert outside_switches.is_off(sid)
    outside_switches.set_switch(sid, True, by="owner")
    assert not outside_switches.is_off(sid)
    outside_switches.set_switch(sid, False, by="owner")
    assert outside_switches.is_off(sid)


@pytest.mark.parametrize("sid", _AS_BUILT)
def test_as_built_services_stay_on_when_the_bridge_is_not_up(fenced, sid):
    assert not outside_switches.is_off(sid)


@pytest.mark.parametrize("sid", _AS_BUILT)
def test_a_sealed_build_uses_nothing_even_when_switched_on(sealed, sid):
    outside_switches.set_switch(sid, True, by="owner")
    assert outside_switches.is_off(sid)


def test_the_box_install_parts_follow_code_downloads(bridge_up):
    assert outside_switches.is_off("box_install")
    outside_switches.set_switch("code_download", True, by="owner")
    assert not outside_switches.is_off("box_install")


def test_a_code_search_is_refused_before_any_request_with_the_bridge_up(bridge_up):
    calls = []

    def transport(url, headers, timeout):  # pragma: no cover - must not run
        calls.append(url)
        raise AssertionError("no request may be made")

    got = source_fetch.fetch_small("https://api.github.com/search/repositories?q=x",
                                   allowed_hosts={"api.github.com"},
                                   max_bytes=1000, service="code_search",
                                   transport=transport,
                                   resolver=lambda h: ["140.82.112.5"])
    assert got["reason"] == "switched_off" and got["body"] is None
    assert calls == []
    rows = egress_ledger.read(1.0)
    assert rows and rows[0]["service"] == "code_search" and rows[0]["code"] == "switched_off"


class _ProbeReply:
    status_code = 200
    headers = {"Content-Type": "application/json"}
    raw = None

    def iter_content(self, chunk_size=1):
        yield b'{"ok":1}'

    def close(self):
        pass


def _probe_would_send(monkeypatch):
    """Everything but the Bridge says yes: the name resolves to a public
    address, the peer check passes and _send answers. So a refusal can only
    come from the Bridge, never from 'cannot resolve' in an offline test."""
    sent = []
    monkeypatch.delenv(egress_doors.BUILD_PROBE_ENV, raising=False)
    monkeypatch.setattr(egress_doors, "_send",
                        lambda m, u, **k: (sent.append((m, u)), _ProbeReply())[1])
    monkeypatch.setattr(egress_doors, "_verify_peer", lambda *a, **k: None)
    return sent


def _public(host, port=443):
    return ["93.184.216.34"]


def test_the_sample_look_is_refused_with_the_bridge_up(bridge_up, monkeypatch):
    sent = _probe_would_send(monkeypatch)
    assert outside_switches.get("sample_look") is None
    with pytest.raises(egress_doors.EgressBlocked) as caught:
        egress_doors.build_probe_get("https://api.example.org/x",
                                     host="api.example.org", build_online=True,
                                     resolver=_public)
    assert caught.value.code == "bridge_up"
    assert "resolve" not in caught.value.reason
    assert not plain_words.check(caught.value.user_message)
    assert sent == []
    rows = egress_ledger.read(1.0)
    assert rows and rows[0]["service"] == "sample_look" \
        and rows[0]["code"] == "bridge_up"


def test_the_sample_look_goes_once_switched_on_with_the_bridge_up(bridge_up, monkeypatch):
    # Control for the test above: the same call, the switch on, is sent.
    sent = _probe_would_send(monkeypatch)
    outside_switches.set_switch("sample_look", True)
    got = egress_doors.build_probe_get("https://api.example.org/x",
                                       host="api.example.org", build_online=True,
                                       resolver=_public)
    assert got["status"] == 200 and sent == [("GET", "https://api.example.org/x")]


def test_the_sample_look_is_refused_on_a_sealed_build_even_when_switched_on(sealed, monkeypatch):
    sent = _probe_would_send(monkeypatch)
    with pytest.raises(egress_doors.EgressBlocked):
        egress_doors.build_probe_get("https://api.example.org/x",
                                     host="api.example.org", build_online=True,
                                     resolver=_public)
    assert sent == []


def test_the_sample_look_is_refused_with_the_bridge_up_on_a_fake_network(bridge_up, monkeypatch):
    # The old version gave no resolver,
    # so it passed on a failed name lookup. This one has a resolver and a
    # transport that answers, so only the Bridge check can refuse it.
    from livedata_support import FakeResponse, fake_network
    net = fake_network(monkeypatch)
    net.on("https://api.example.org/x", FakeResponse(200, b"{}"))
    with pytest.raises(egress_doors.EgressBlocked) as exc:
        egress_doors.build_probe_get("https://api.example.org/x",
                                     host="api.example.org", build_online=True)
    assert exc.value.code == "bridge_up"   # the Bridge's own log code
    assert exc.value.user_message == egress_doors.SAMPLE_LOOK_HELD_LINE
    assert net.calls == []
    # Switched on, the same look goes out: the Bridge was the only reason.
    outside_switches.set_switch("sample_look", True, by="owner")
    got = egress_doors.build_probe_get("https://api.example.org/x",
                                       host="api.example.org", build_online=True)
    assert got["status"] == 200 and len(net.calls) == 1


def test_the_sample_look_is_refused_when_the_bridge_cannot_be_read(bridge_up, monkeypatch):
    sent = _probe_would_send(monkeypatch)
    monkeypatch.setattr(bridge_guard, "switch_allows",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("unreadable")))
    with pytest.raises(egress_doors.EgressBlocked) as caught:
        egress_doors.build_probe_get("https://api.example.org/x",
                                     host="api.example.org", build_online=True,
                                     resolver=_public)
    assert caught.value.code == "bridge_up" and sent == []


def test_a_card_look_with_its_own_probe_is_refused_with_the_bridge_up(bridge_up):
    # feed_generator._look passes build_online from the build posture, which
    # does not know the Bridge; it must ask the Bridge itself.
    from puffpai import feed_generator
    called = []
    cand = {"id": "c1", "host": "api.example.org", "name": "Example"}
    with pytest.raises(feed_generator.NotLooked) as caught:
        feed_generator._look(cand, "https://api.example.org/x", build_online=True,
                             probe=lambda *a, **k: called.append(a) or {})
    assert called == []
    assert "Bridge" in str(caught.value)


def test_the_outside_page_says_why_an_as_built_row_is_off(bridge_up):
    row = outside_services.row("code_search")
    assert row["state"] == "off" and row["can_switch"] is True
    assert row["state_line"] == outside_services.BRIDGE_UP_ROW_LINE
    assert not plain_words.check(row["state_line"])
    outside_services.switch_on("code_search")
    assert outside_services.row("code_search")["state"] == "on"


def test_on_a_sealed_build_an_as_built_row_cannot_be_switched_on(sealed):
    row = outside_services.row("code_search")
    assert row["state"] == "off" and row["can_switch"] is False
    with pytest.raises(outside_services.OutsideError):
        outside_services.switch_on("code_search")


# ── outside services the owner names, and live cards ────────────────────────

def test_an_environment_yes_never_opens_a_service_with_the_bridge_up(bridge_up, monkeypatch):
    monkeypatch.setenv("PUFFPAI_EGRESS_WEATHER", "on")
    assert egress_doors.egress_allowed("weather")[0] is False
    outside_switches.set_switch("weather", True, by="owner")
    assert egress_doors.egress_allowed("weather")[0] is True
    monkeypatch.setenv("PUFFPAI_EGRESS_WEATHER", "off")
    assert egress_doors.egress_allowed("weather")[0] is False


def test_an_environment_yes_still_works_when_the_bridge_is_not_up(fenced, monkeypatch):
    monkeypatch.setenv("PUFFPAI_EGRESS_WEATHER", "on")
    assert egress_doors.egress_allowed("weather")[0] is True


def _settings_file(flags: dict) -> None:
    cfg_path = os.path.join(get_config().CONFIG_DIR, "puffpai_config.json")
    with open(cfg_path, "w", encoding="utf-8") as fh:
        json.dump({"egress_doors": flags}, fh)
    egress_doors.reset_cache()


def test_a_settings_file_yes_never_opens_a_service_with_the_bridge_up(bridge_up):
    """The built-in services still opened on an
    egress_doors.<flag> = true line, while live cards did not. Same rule now:
    only the owner's own switch (or her answer in the wizard) opens one."""
    door = egress_doors.door("weather")
    _settings_file({door.config_flag: True})
    allowed, why = egress_doors.egress_allowed("weather")
    assert allowed is False and "Bridge is up" in why
    assert outside_services.row("weather")["state"] == "off"
    outside_switches.set_switch("weather", True, by="owner")
    assert egress_doors.egress_allowed("weather")[0] is True
    outside_switches.set_switch("weather", False, by="owner")
    assert egress_doors.egress_allowed("weather")[0] is False


def test_a_settings_file_no_still_shuts_a_service_with_the_bridge_up(bridge_up):
    door = egress_doors.door("weather")
    _settings_file({door.config_flag: False})
    decided, _why = egress_doors._switch(door)
    assert decided is False


def test_a_settings_file_yes_still_works_when_the_bridge_is_not_up(fenced):
    door = egress_doors.door("weather")
    _settings_file({door.config_flag: True})
    assert egress_doors.egress_allowed("weather")[0] is True


def test_the_wizards_answer_opens_a_service_with_the_bridge_up():
    """Her answer in the wizard lives in the build record beside the Bridge
    choice itself, and shows on What leaves your computer as switched on
    when the bot was made; she can switch it off there."""
    _write_manifest("drawbridge_up")
    path = os.path.join(get_config().CONFIG_DIR, "build_manifest.json")
    data = json.load(open(path, encoding="utf-8"))
    data["posture"]["egress_switches"] = {"weather": True}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    bridge_guard.forget_enforced()
    _settings_file({})
    try:
        assert bridge_guard.reach_level() == "switched_on"
        assert egress_doors.egress_allowed("weather")[0] is True
        outside_switches.set_switch("weather", False, by="owner")
        assert egress_doors.egress_allowed("weather")[0] is False
    finally:
        bridge_guard.forget_enforced()


def _card():
    egress_doors.register_app_doors([
        ("skyshots", {"hosts": ["api.example.org"], "name": "Sky shots",
                      "purpose": "pictures", "data_sent": "nothing"})])
    return egress_doors.app_door("skyshots")


def test_a_live_card_opens_only_on_the_owners_own_yes_with_the_bridge_up(bridge_up, monkeypatch):
    door = _card()
    try:
        monkeypatch.setenv(door.env_var, "on")
        assert egress_doors.egress_allowed(door.id)[0] is False
        monkeypatch.delenv(door.env_var)
        cfg_path = os.path.join(get_config().CONFIG_DIR, "puffpai_config.json")
        with open(cfg_path, "w", encoding="utf-8") as fh:
            json.dump({"egress_doors": {door.config_flag: True}}, fh)
        egress_doors.reset_cache()
        assert egress_doors.egress_allowed(door.id)[0] is False
        egress_doors.set_app_door_consent("skyshots", True)
        egress_doors.reset_cache()
        assert egress_doors.egress_allowed(door.id)[0] is True
    finally:
        egress_doors.clear_app_doors()


def test_app_traffic_needs_the_owners_yes_for_that_site(bridge_up):
    egress_doors.register_web_app_door("seating-abcdefgh", ["api.example.org"])
    try:
        got = egress_doors.app_web_request("seating-abcdefgh", "GET",
                                           "https://api.example.org/x")
        assert got.get("refused") in ("switched_off", "unknown_app")
    finally:
        egress_doors.unregister_web_app_door("seating-abcdefgh")


# ── the locked box ───────────────────────────────────────────────────────────

@pytest.fixture
def no_box(monkeypatch):
    started: list = []
    monkeypatch.setattr(driver, "_platform", lambda: "windows")
    monkeypatch.setattr(driver, "_box_ready_for_run", lambda d: None)
    monkeypatch.setattr(driver, "_run_wsl", lambda d, req, **k: started.append(req)
                        or driver.BoxResult(ok=True))
    return started


def _box(what, hosts):
    return driver.BoxRequest(run_id="r1234", project="seating-planner",
                             argv=["npm", "ci"], allow_hosts=list(hosts),
                             what=what)


def test_the_box_cannot_fetch_install_parts_with_the_bridge_up(bridge_up, no_box):
    res = driver.run(_box("box_install", ["registry.npmjs.org"]))
    assert res.refused == "switched_off" and no_box == []
    outside_switches.set_switch("code_download", True, by="owner")
    assert driver.run(_box("box_install", ["registry.npmjs.org"])).ok
    assert len(no_box) == 1


def test_the_box_install_parts_are_as_built_without_the_bridge(fenced, no_box):
    assert driver.run(_box("box_install", ["registry.npmjs.org"])).ok


def test_a_switched_on_box_project_still_reaches_nothing_on_a_sealed_build(sealed, no_box):
    outside_switches.set_switch(driver.service_for("seating-planner"), True, by="owner")
    res = driver.run(_box("box_traffic", ["api.example.com"]))
    assert res.refused == "switched_off" and no_box == []


# ── the brain and the books, at home ────────────────────────────────────────

@pytest.mark.parametrize("url", ["http://192.168.1.50:11434", "http://gpu-box.local:11434",
                                 "http://ollama:11434"])
def test_with_the_bridge_up_the_brain_must_be_on_this_computer(url):
    found = lan_posture.check_url("ollama", url, "island", bridge_level="switched_on")
    assert found is not None and found.posture == "drawbridge_up"
    assert "Bridge is up" in found.user_message
    assert not plain_words.check(found.user_message.split(" is set to ")[1])
    assert lan_posture.check_url("ollama", url, "island", bridge_level="open") is None


def test_the_brain_on_this_computer_is_fine_with_the_bridge_up():
    for url in ("http://127.0.0.1:11435", "http://localhost:11434", "http://[::1]:11434"):
        assert lan_posture.check_url("ollama", url, "island",
                                     bridge_level="switched_on") is None


def test_home_assistant_switched_on_is_allowed_with_the_bridge_up():
    assert lan_posture.check_url("home_assistant", "http://192.168.1.20:8123",
                                 "island", bridge_level="switched_on") is None


def test_loading_the_settings_resets_a_home_brain_with_the_bridge_up(bridge_up):
    cfg = types.SimpleNamespace(
        CONFIG_DIR=get_config().CONFIG_DIR,
        ollama=types.SimpleNamespace(url="http://192.168.1.50:11434"),
        ha=types.SimpleNamespace(enabled=True, url="http://192.168.1.20:8123"),
        n8n=types.SimpleNamespace(enabled=False, url="http://192.168.1.21:5678"),
        kiwix=types.SimpleNamespace(url="http://192.168.1.22:8080"),
        media_server=types.SimpleNamespace(enabled=False, jellyfin_url="",
                                           calibre_web_url=""),
        dashboard=types.SimpleNamespace(url="http://192.168.1.30:3000"))
    found = lan_posture.enforce(cfg, "island")
    assert {v.service for v in found} == {"ollama", "kiwix"}
    assert cfg.ollama.url.startswith("http://localhost")
    assert cfg.kiwix.url == ""
    assert cfg.ha.enabled is True and cfg.dashboard.url.endswith(":3000")


# ── the connect-time check ───────────────────────────────────────────────────

class _Sock:
    family = __import__("socket").AF_INET


@pytest.fixture
def hook():
    lan_guard.install()
    bridge_guard.forget_names()
    yield
    bridge_guard.forget_names()


@pytest.mark.parametrize("addr", [("8.8.8.8", 443), ("140.82.112.5", 443),
                                  ("example.com", 443), ("192.168.1.50", 11434)])
def test_with_the_bridge_up_nothing_unlisted_leaves_this_computer(bridge_up, hook, addr):
    before = len(egress_ledger.read(1.0))
    with pytest.raises(bridge_guard.BridgeRefused) as exc:
        _PROBES.unmarked(addr, _Sock())
    assert exc.value.user_message == zone_bridge.REFUSED_LINE
    rows = egress_ledger.read(1.0)
    assert len(rows) == before + 1 and rows[0]["code"] == "bridge_up"
    line = outside_services.log_rows(1.0)[0]["line"]
    assert "the Bridge is up, and this was not switched on" in line
    assert not plain_words.check(line)


def test_without_the_bridge_the_connect_check_changes_nothing(fenced, hook):
    _PROBES.unmarked(("8.8.8.8", 443), _Sock())  # judged by the exits, as before
    _PROBES.marked(("192.168.1.50", 11434), _Sock())


def test_this_computer_is_always_fine(sealed, hook):
    for addr in (("127.0.0.1", 11435), ("::1", 8000, 0, 0), ("localhost", 3000)):
        _PROBES.unmarked(addr, _Sock())
    _PROBES.unmarked("/run/some.sock")  # not an internet socket at all


def test_a_listed_exit_is_trusted_to_have_checked_its_switch(bridge_up, hook):
    _EXIT_PROBES.dial(("140.82.112.5", 443), _Sock())


def test_a_sealed_build_refuses_even_a_listed_exit(sealed, hook):
    with pytest.raises(bridge_guard.BridgeRefused) as exc:
        _EXIT_PROBES.dial(("140.82.112.5", 443), _Sock())
    assert exc.value.user_message == zone_bridge.SEALED_LINE


def _home(ha_on: bool, url: str = "http://192.168.1.20:8123"):
    cfg = get_config()
    cfg.ha.enabled = ha_on
    cfg.ha.url = url


def test_home_assistant_is_reached_only_while_it_is_switched_on(bridge_up, hook):
    _home(True)
    _PROBES.marked(("192.168.1.20", 8123), _Sock())
    with pytest.raises(bridge_guard.BridgeRefused):
        _PROBES.marked(("192.168.1.20", 22), _Sock())      # another port
    with pytest.raises(bridge_guard.BridgeRefused):
        _PROBES.marked(("192.168.1.21", 8123), _Sock())    # another device
    _home(False)
    with pytest.raises(bridge_guard.BridgeRefused):
        _PROBES.marked(("192.168.1.20", 8123), _Sock())


def test_an_unmarked_call_to_home_assistant_is_still_refused(bridge_up, hook):
    _home(True)
    with pytest.raises(bridge_guard.BridgeRefused):
        _PROBES.unmarked(("192.168.1.20", 8123), _Sock())


def test_a_home_assistant_name_is_matched_by_what_it_looks_up_to(bridge_up, hook, monkeypatch):
    _home(True, "http://homeassistant.local:8123")
    monkeypatch.setattr(bridge_guard.socket, "getaddrinfo",
                        lambda host, port, *a, **k: [(2, 1, 6, "", ("192.168.1.20", port))])
    _PROBES.marked(("192.168.1.20", 8123), _Sock())
    with pytest.raises(bridge_guard.BridgeRefused):
        _PROBES.marked(("192.168.1.99", 8123), _Sock())


def test_a_real_socket_is_refused_before_a_packet(bridge_up, hook):
    """The hook acts on the socket.connect event, which comes before the
    syscall; the stand-in raises that event the same way connect() does."""
    import socket
    seen = []

    def _raw_connect(sock, address):
        sys.audit("socket.connect", sock, address)
        seen.append(address)
        return 0

    mod = _module_at(os.path.join(conftest._SRC_ROOT, "zz_dev_sec_real.py"),
                     "zz_dev_sec_real",
                     "import socket\n"
                     "def go(connect):\n"
                     "    s = socket.socket()\n"
                     "    try:\n"
                     "        return connect(s, ('203.0.114.7', 443))\n"
                     "    finally:\n"
                     "        s.close()\n")
    with pytest.raises(bridge_guard.BridgeRefused):
        mod.go(_raw_connect)
    assert seen == []


# ── asyncio's Windows loop ─────────────────────────
# The Proactor loop dials with ConnectEx/WSAConnect, never socket.connect, so
# the audit hook used to miss it: asyncio.open_connection reached this PC's
# home address with the Bridge up. lan_guard.install() now makes it raise the
# same event first. The real connect is swapped for a recorder, so nothing is
# dialled when the check fails either.

_windows_only = pytest.mark.skipif(sys.platform != "win32",
                                   reason="the Proactor loop is Windows only")


def _proactor_dial(host, port, kind="tcp"):
    import asyncio

    async def go():
        loop = asyncio.get_running_loop()
        if kind == "udp":
            await loop.create_datagram_endpoint(asyncio.DatagramProtocol,
                                                remote_addr=(host, port))
            return
        await asyncio.wait_for(asyncio.open_connection(host, port), 5)

    loop = asyncio.ProactorEventLoop()
    try:
        return loop.run_until_complete(go())
    finally:
        loop.close()


@pytest.fixture
def no_dial(monkeypatch):
    """The Proactor's real connect, replaced by a recorder that dials nothing."""
    calls = []

    def _record(self, conn, address):
        calls.append(address)
        raise ConnectionRefusedError("test: never dialled")

    if not lan_guard.proactor_guarded():
        # Without the wrapper the recorder is never called and the loop would
        # really dial: fail here instead, with nothing sent.
        pytest.fail("the Windows loop's connect is not under the check")
    monkeypatch.setattr(lan_guard, "_proactor_real_connect", _record)
    return calls


@_windows_only
def test_install_puts_the_windows_loop_under_the_check(hook):
    from asyncio import windows_events
    assert lan_guard.proactor_guarded()
    assert windows_events.IocpProactor.connect is lan_guard._proactor_connect
    lan_guard.install()   # again: never wrapped twice
    assert windows_events.IocpProactor.connect is lan_guard._proactor_connect


@_windows_only
@pytest.mark.parametrize("host, port", [("198.51.100.7", 80), ("10.255.255.1", 8123),
                                        ("192.168.1.50", 11434), ("2001:db8::1", 443)])
def test_asyncio_on_windows_is_refused_before_it_dials(bridge_up, hook, no_dial, host, port):
    before = len(egress_ledger.read(1.0))
    with pytest.raises(bridge_guard.BridgeRefused) as exc:
        _proactor_dial(host, port)
    assert exc.value.user_message == zone_bridge.REFUSED_LINE
    assert no_dial == []                       # the real connect never ran
    rows = egress_ledger.read(1.0)
    assert len(rows) == before + 1 and rows[0]["code"] == "bridge_up"


@_windows_only
def test_asyncio_udp_on_windows_is_refused_too(bridge_up, hook, no_dial):
    with pytest.raises(bridge_guard.BridgeRefused):
        _proactor_dial("198.51.100.7", 53, kind="udp")
    assert no_dial == []


@_windows_only
def test_asyncio_on_a_sealed_build_is_refused_on_windows(sealed, hook, no_dial):
    with pytest.raises(bridge_guard.BridgeRefused) as exc:
        _proactor_dial("198.51.100.7", 443)
    assert exc.value.user_message == zone_bridge.SEALED_LINE and no_dial == []


@_windows_only
def test_asyncio_without_the_bridge_goes_on_to_the_real_connect(fenced, hook, no_dial):
    with pytest.raises(ConnectionRefusedError) as exc:
        _proactor_dial("198.51.100.7", 80)
    assert not isinstance(exc.value, bridge_guard.BridgeRefused)
    assert no_dial == [("198.51.100.7", 80)]   # judged, then handed on


@_windows_only
def test_asyncio_on_windows_still_reaches_this_computer(sealed, hook):
    """A real loopback connection through the wrapped Proactor connect."""
    import socket
    import threading
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    got = []
    threading.Thread(target=lambda: got.append(srv.accept()[0].close()),
                     daemon=True).start()
    try:
        _proactor_dial("127.0.0.1", port)
    finally:
        srv.close()
    assert got == [None]


# ── a home DNS filter is named as one ───────────────────────────────────────

@pytest.fixture
def dns_server(monkeypatch):
    """This computer's DNS server, as the network settings would say."""
    monkeypatch.setattr(pinned_net, "resolver_addresses",
                        lambda: frozenset({"192.168.1.2"}))
    return "192.168.1.2"


@pytest.mark.parametrize("answers", [["0.0.0.0"], ["::"], ["0.0.0.0", "::"],
                                     ["::ffff:0.0.0.0"], ["127.0.0.1"], ["::1"],
                                     ["127.0.0.1", "::1"], ["0.0.0.0", "127.0.0.1"],
                                     ["192.168.1.2"], ["192.168.1.2", "::"]])
def test_a_filtered_name_reads_as_blocked_by_the_network_filter(answers, dns_server):
    """0.0.0.0 / :: (Pi-hole, AdGuard Home), 127.0.0.1 / ::1 (hosts-file
    blockers) and the DNS server's own address (Pi-hole's "IP" mode) are all
    a filter's block: never "pointed inside your home network"."""
    assert pinned_net.filter_block(answers)
    with pytest.raises(pinned_net.PinRefused) as exc:
        pinned_net.resolve_public("ads.example.com", 443, lambda h, p: answers)
    assert exc.value.code == "network_filter"
    egress_ledger.record("weather", host="ads.example.com", outcome="refused",
                         code=exc.value.code)
    line = outside_services.log_rows(1.0)[0]["line"]
    assert "blocked by your network's filter" in line
    assert "pointed inside your home network" not in line


@pytest.mark.parametrize("answers", [["10.0.0.5"], ["0.0.0.0", "10.0.0.5"],
                                     ["0.0.0.0", "93.184.216.34"],
                                     ["192.168.1.3"], ["127.0.0.1", "192.168.1.9"]])
def test_a_real_inside_address_is_not_called_a_filter(answers, dns_server):
    assert not pinned_net.filter_block(answers)
    with pytest.raises(pinned_net.PinRefused) as exc:
        pinned_net.resolve_public("x.example.com", 443, lambda h, p: answers)
    assert exc.value.code == "inside_address"


@pytest.mark.parametrize("host", ["localhost", "app.localhost", "nas.local",
                                  "printer", "router.lan", "hub.home.arpa"])
def test_a_name_meant_to_be_at_home_is_never_called_a_filter(host, dns_server):
    for answers in (["127.0.0.1"], ["0.0.0.0"], ["192.168.1.2"]):
        assert not pinned_net.filter_block(answers, host)
    with pytest.raises(pinned_net.PinRefused) as exc:
        pinned_net.resolve_public(host, 443, lambda h, p: ["127.0.0.1"])
    assert exc.value.code == "inside_address"


def test_a_public_dns_server_answering_itself_is_not_a_filter(monkeypatch):
    """dns.google answering 8.8.8.8 is a public answer, not a block."""
    monkeypatch.setattr(pinned_net, "resolver_addresses",
                        lambda: frozenset({"8.8.8.8"}))
    assert not pinned_net.filter_block(["8.8.8.8"], "dns.google")
    assert pinned_net.resolve_public("dns.google", 443,
                                     lambda h, p: ["8.8.8.8"]) == ["8.8.8.8"]


def test_the_dns_servers_are_read_from_the_network_settings(monkeypatch):
    pinned_net.forget_resolvers()
    reader = ("_resolver_lines_windows" if sys.platform == "win32"
              else "_resolver_lines_posix")
    monkeypatch.setattr(pinned_net, reader,
                        lambda: ["192.168.1.2 fd00::53", "10.0.0.1,junk", ""])
    try:
        assert pinned_net.resolver_addresses() == frozenset(
            {"192.168.1.2", "fd00::53", "10.0.0.1"})
        pinned_net.forget_resolvers()
        monkeypatch.setattr(pinned_net, reader, lambda: 1 / 0)
        assert pinned_net.resolver_addresses() == frozenset()   # never raises
    finally:
        pinned_net.forget_resolvers()


def test_the_posix_dns_server_list_is_read_from_resolv_conf(monkeypatch, tmp_path):
    conf = tmp_path / "resolv.conf"
    conf.write_text("# made by NetworkManager\nsearch lan\nnameserver 192.168.1.2\n"
                    "nameserver fe80::1%eth0\noptions edns0\n", encoding="utf-8")
    real_open = open
    gone = str(tmp_path / "no-such-resolv.conf")   # a real Linux has systemd-resolved's too
    places = {"/etc/resolv.conf": str(conf), "/run/systemd/resolve/resolv.conf": gone}
    monkeypatch.setattr("builtins.open", lambda path, *a, **k: real_open(
        places.get(path, path), *a, **k))
    assert pinned_net._resolver_lines_posix() == ["192.168.1.2", "fe80::1%eth0"]


# ── the locked box names a filter's block the same way ─────────────────────

def _box_proxy():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "dev_sec_box_proxy", os.path.join(os.path.dirname(driver.__file__),
                                          "assets", "egress_proxy.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("answers", [["0.0.0.0"], ["::"], ["127.0.0.1"], ["::1"],
                                     ["0.0.0.0", "::"], ["192.168.50.1"]])
def test_the_box_proxy_names_a_filter_block(answers):
    proxy = _box_proxy()
    code = proxy.answer_refusal("tracker.example.com", answers,
                                servers={"192.168.50.1"})
    assert code == "network_filter"
    n = driver.ingest_outside([{"service": "box_" + "a" * 24, "what": "box_traffic",
                                "host": "tracker.example.com", "outcome": "refused",
                                "code": code, "method": "CONNECT"}])
    assert n == 1
    line = outside_services.log_rows(1.0)[0]["line"]
    assert "blocked by your network's filter" in line
    assert "pointed inside your home network" not in line


@pytest.mark.parametrize("host, answers, code", [
    ("tracker.example.com", ["10.0.0.5"], "inside_address"),
    ("tracker.example.com", ["0.0.0.0", "10.0.0.5"], "inside_address"),
    ("tracker.example.com", ["0.0.0.0", "93.184.216.34"], "inside_address"),
    ("tracker.example.com", [], "inside_address"),
    ("app.localhost", ["127.0.0.1"], "inside_address"),
    ("nas.local", ["0.0.0.0"], "inside_address"),
    ("registry.npmjs.org", ["104.16.0.1"], ""),
])
def test_the_box_proxy_keeps_its_other_answers(host, answers, code):
    assert _box_proxy().answer_refusal(host, answers, servers={"192.168.50.1"}) == code


def test_the_box_proxy_reads_its_dns_servers(tmp_path):
    conf = tmp_path / "resolv.conf"
    conf.write_text("nameserver 192.168.50.1\nnameserver junk\n", encoding="utf-8")
    proxy = _box_proxy()
    assert proxy.nameservers((str(conf), str(tmp_path / "missing"))) == {"192.168.50.1"}


def test_every_exit_names_a_filter_block_the_same_way():
    assert source_fetch._hop_code("https://api.github.com/x", {"api.github.com"},
                                  lambda h: ["0.0.0.0"]) == "network_filter"
    ok, why = source_fetch.guard_url("https://api.github.com/x",
                                     allowed_hosts={"api.github.com"},
                                     resolver=lambda h: ["0.0.0.0"])
    assert not ok and "blocked by your network's filter" in why
    with pytest.raises(egress_doors.EgressBlocked) as exc:
        egress_doors._check_resolved_public(egress_doors.door("news_rss"),
                                            "feeds.example.com", 443,
                                            resolver=lambda h, p: ["::"])
    assert exc.value.code == "network_filter"
    assert "network_filter" in egress_doors.WEB_REFUSALS
    assert "network_filter" in egress_ledger.REASON_CODES


# ── the words never promise more than is enforced ───────────────────────────

def test_the_shipped_words_are_the_code_words():
    with open(zone_bridge._postures_path(), encoding="utf-8") as fh:
        shipped = {p["id"]: p for p in json.load(fh)["postures"]}
    for entry in zone_bridge._FALLBACK_POSTURES["postures"]:
        for key in ("label", "summary", "allows", "costs"):
            assert shipped[entry["id"]][key] == entry[key], (entry["id"], key)


@pytest.mark.parametrize("pid", ["open_yard", "fenced", "drawbridge_up"])
def test_no_posture_claims_more_than_is_enforced(pid):
    entry = zone_bridge.posture_by_id(pid)
    words = " ".join([entry["label"], entry["summary"], entry["costs"],
                      *entry["allows"].values()])
    for claim in ("Nothing leaves this computer, ever", "Nothing can be switched on",
                  "Never ask again", "without asking each time",
                  "every one starts off", "Actions are checked before they run"):
        assert claim not in words, (pid, claim)
    assert "—" not in words and "door" not in words.lower()
    assert not plain_words.check(words)


def test_the_drawbridge_words_say_what_it_does():
    entry = zone_bridge.posture_by_id("drawbridge_up")
    assert "except the services you switched on" in entry["summary"]
    assert "Home Assistant counts only if you switched it on" in entry["summary"]


def test_an_old_record_shows_todays_words():
    old = {"posture": "drawbridge_up", "label": "Nothing outside this computer",
           "summary": "Nothing leaves this computer, ever, and the bot says so."}
    now = zone_bridge.current_words(old)
    assert now["summary"] == zone_bridge.posture_by_id("drawbridge_up")["summary"]
    assert "ever" not in now["summary"]


def test_the_build_self_page_says_what_is_enforced(bridge_up):
    from puffpai import api_server
    block = api_server._build_self_bridge({"bridge": {"posture": "drawbridge_up"},
                                           "posture": {"run_egress": "enumerated"}})
    assert block["enforced_level"] == "switched_on"
    assert block["enforced_line"] == zone_bridge.ENFORCED_LINES["switched_on"]
    assert block["summary"] == zone_bridge.posture_by_id("drawbridge_up")["summary"]
