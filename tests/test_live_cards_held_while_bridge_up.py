"""Live cards are held while the Bridge is up.

* Live cards went out with the Bridge up. The plan's preview, the Add
  page and the wizard's build all made the sample look while "What leaves your
  computer" said it was off. Each path is driven here with a resolver and a
  transport that answers, so the only thing that can stop it is the Bridge
  check; each has a control that shows the same path does reach the transport
  when the Bridge is not up.
* A proxy named by a request target two parsers read differently, or by a
  tunnel host set by hand, is refused with the Bridge up.
* A search refused while "Looking for apps and code" was off is searched again
  once it is switched on.
* The PIN rule: a keypad shape with a character put in anywhere, a shape said
  twice, dates, phone shapes, number sequences, and a word with a number or a
  year on the end.

No network: every fetch uses a stand-in transport that records what it was
asked, and the proxy tests talk only to a listener on 127.0.0.1.
"""
from __future__ import annotations

import json
import os

import pytest

from livedata_support import FakeResponse, fake_network
from puffpai.catalogs import api_seeds
from puffpai.config import get_config
from puffpai.security import bridge_guard, egress_doors, outside_switches

SEEDS = ("seed:open_meteo", "seed:nasa_images", "seed:artic")


def _write_manifest(bridge: str) -> None:
    cfg = get_config()
    os.makedirs(cfg.CONFIG_DIR, exist_ok=True)
    with open(os.path.join(cfg.CONFIG_DIR, "build_manifest.json"), "w",
              encoding="utf-8") as fh:
        json.dump({"posture": {"build_network": "online",
                               "run_egress": "enumerated"},
                   "bridge": {"posture": bridge}}, fh)
    bridge_guard.forget_enforced()
    egress_doors.reset_cache()
    outside_switches.forget()


@pytest.fixture
def bridge_up():
    _write_manifest("drawbridge_up")
    yield
    bridge_guard.forget_enforced()


@pytest.fixture
def fenced():
    _write_manifest("fenced")
    yield
    bridge_guard.forget_enforced()


def _seed(cid: str) -> dict:
    return next(c for c in api_seeds.candidates() if c["id"] == cid)


def _answering_network(monkeypatch):
    """A resolver and a transport that answers every seed's address."""
    net = fake_network(monkeypatch)
    for cid in SEEDS:
        cand = _seed(cid)
        net.on(f"https://{cand['host']}/", FakeResponse(200, b"{}"))
    return net


# ── BLOCKER: every live-card path asks the Bridge ───────────────────────────

@pytest.mark.parametrize("cid", SEEDS)
def test_the_plan_preview_makes_no_look_with_the_bridge_up(bridge_up, monkeypatch, cid):
    from puffpai import feed_generator
    net = _answering_network(monkeypatch)
    got = feed_generator.preview(_seed(cid), build_online=True)
    assert got["looked"] is False and got["ok"] is False
    assert got["why"] == egress_doors.SAMPLE_LOOK_HELD_LINE
    assert net.calls == []


@pytest.mark.parametrize("cid", SEEDS)
def test_the_plan_preview_does_look_when_the_bridge_is_not_up(fenced, monkeypatch, cid):
    from puffpai import feed_generator
    net = _answering_network(monkeypatch)
    feed_generator.preview(_seed(cid), build_online=True)
    assert len(net.calls) == 1, "the control: this path does reach the transport"


@pytest.mark.parametrize("cid", SEEDS)
def test_the_add_page_live_card_makes_no_look_with_the_bridge_up(bridge_up, monkeypatch, cid):
    from puffpai import add_more
    net = _answering_network(monkeypatch)
    monkeypatch.setattr(add_more, "online_allowed", lambda: True)
    ctx = add_more.ApplyContext(via="page", by="owner", surface="page",
                                words="pictures of birds", candidate=_seed(cid))
    got = add_more._apply_live({"label": "A live card"}, ctx)
    assert got["status"] == "held"
    assert egress_doors.SAMPLE_LOOK_HELD_LINE in got["line"]
    assert net.calls == []


def test_the_add_page_live_card_does_look_when_the_bridge_is_not_up(fenced, monkeypatch):
    from puffpai import add_more
    net = _answering_network(monkeypatch)
    monkeypatch.setattr(add_more, "online_allowed", lambda: True)
    ctx = add_more.ApplyContext(via="page", by="owner", surface="page",
                                words="weather", candidate=_seed("seed:open_meteo"))
    add_more._apply_live({"label": "A live card"}, ctx)
    assert len(net.calls) == 1


def test_a_caller_with_its_own_probe_is_held_too(bridge_up):
    from puffpai import feed_generator
    asked = []

    def probe(url, host, build_online, max_bytes):  # pragma: no cover - must not run
        asked.append(url)
        raise AssertionError("no look may be made")

    with pytest.raises(feed_generator.NotLooked):
        feed_generator.draft_feed_app(_seed("seed:open_meteo"), build_online=True,
                                      probe=probe)
    assert asked == []


def test_switched_on_the_look_goes_out_with_the_bridge_up(bridge_up, monkeypatch):
    from puffpai import feed_generator
    net = _answering_network(monkeypatch)
    outside_switches.set_switch("sample_look", True, by="owner")
    feed_generator.preview(_seed("seed:open_meteo"), build_online=True)
    assert len(net.calls) == 1


def test_an_unreadable_switch_holds_the_look(monkeypatch):
    def broken(_sid):
        raise OSError("unreadable")
    monkeypatch.setattr(outside_switches, "is_off", broken)
    assert egress_doors.sample_look_held() is True


# ── a proxy named in ways two readers see differently ───────────────────────

@pytest.fixture
def listener(monkeypatch):
    """A fake proxy on this computer that records every request line it gets."""
    import socket
    import threading
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy",
                 "https_proxy", "all_proxy", "NO_PROXY", "no_proxy"):
        monkeypatch.delenv(name, raising=False)
    from puffpai.security import lan_guard
    lan_guard.install()
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(8)
    srv.settimeout(0.3)
    asked: list[str] = []
    stop = threading.Event()

    def serve():
        while not stop.is_set():
            try:
                conn, _ = srv.accept()
            except OSError:
                continue
            try:
                conn.settimeout(1)
                line = conn.recv(300).split(b"\r\n")[0].decode(errors="replace")
                if line:
                    asked.append(line)
            except OSError:
                pass
            finally:
                conn.close()

    worker = threading.Thread(target=serve, daemon=True)
    worker.start()
    yield srv.getsockname()[1], asked
    stop.set()
    srv.close()
    worker.join(2)


def _send_line(port: int, method: str, target: str, tunnel: str = "") -> None:
    import http.client
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
    if tunnel:
        # Set by hand, without set_tunnel (the acceptance probe's way in).
        conn._tunnel_host, conn._tunnel_port, conn._tunnel_headers = tunnel, 443, {}
    try:
        conn.request(method, target)
        conn.getresponse()
    finally:
        conn.close()


def _settle():
    import time
    time.sleep(0.4)


@pytest.mark.parametrize("target", [
    "http://203.0.113.77" + chr(92) + "@127.0.0.1/bs",   # browser-style: 203.0.113.77
    "http://203.0.113.77%5C@127.0.0.1/bs",
    "http://user@127.0.0.1/ui",                 # a name before @
    "http://127.0.0.1@203.0.113.77/ui",
    "http://203.0.113.77\t/tab",
])
def test_a_target_two_readers_see_differently_is_refused(bridge_up, listener, target):
    port, asked = listener
    with pytest.raises(bridge_guard.BridgeRefused):
        _send_line(port, "GET", target)
    _settle()
    assert asked == []


def test_a_tunnel_host_set_by_hand_is_refused_when_it_is_sent(bridge_up, listener):
    port, asked = listener
    with pytest.raises(bridge_guard.BridgeRefused):
        _send_line(port, "GET", "/x", tunnel="203.0.113.77")
    _settle()
    assert asked == [], "no CONNECT reached the proxy"


def test_the_tunnel_writer_is_guarded():
    assert "http.client.HTTPConnection._tunnel" in \
        bridge_guard.guard_proxy_entry_points()


def test_a_plain_request_on_this_computer_still_goes(bridge_up, listener):
    import http.client
    port, asked = listener
    with pytest.raises((http.client.HTTPException, OSError)) as exc:
        _send_line(port, "GET", "/ok")
    assert not isinstance(exc.value, bridge_guard.BridgeRefused)
    _settle()
    assert asked == ["GET /ok HTTP/1.1"]


def test_with_the_bridge_not_up_the_same_target_is_not_judged(fenced, listener):
    import http.client
    port, asked = listener
    with pytest.raises((http.client.HTTPException, OSError)) as exc:
        _send_line(port, "GET", "http://203.0.113.77" + chr(92) + "@127.0.0.1/bs")
    assert not isinstance(exc.value, bridge_guard.BridgeRefused)


# ── a search refused while a finding tool was off is not remembered ─────────

def test_a_refused_search_is_searched_again_after_switch_on(bridge_up, monkeypatch):
    from puffpai.wizard import find_parts
    asked = []
    monkeypatch.setattr(find_parts, "_fallback",
                        lambda goal, online, cfg: asked.append(online) or [])
    needs = {"schema": "needs/v1", "jobs": [], "goal": "dev-sec final memo"}
    find_parts.plan_items(needs, goal="yarn weight chart", online=True,
                          allow_send=True, cfg=None)
    find_parts.plan_items(needs, goal="yarn weight chart", online=True,
                          allow_send=True, cfg=None)
    assert len(asked) == 1, "the same words, same switches: answered from memory"
    outside_switches.set_switch("code_search", True, by="owner")
    find_parts.plan_items(needs, goal="yarn weight chart", online=True,
                          allow_send=True, cfg=None)
    assert len(asked) == 2, "switched on: the words are searched again"


def test_the_add_page_says_plainly_when_github_was_not_searched(bridge_up, monkeypatch):
    from puffpai import add_more, plain_words
    from puffpai.wizard import find_parts
    monkeypatch.setattr(find_parts, "plan_items", lambda *a, **k: {
        "items": [], "notes": {}, "sends": []})
    ctx = add_more.AddContext(words="yarn weight chart", terms=["yarn"], via="page",
                              online=True, bot_name="Sky Pal",
                              needs={"schema": "needs/v1", "jobs": []},
                              code_online=True)
    add_more._found_parts("yarn weight chart", ctx)
    assert ctx.searched == ["lists of apps and code on this computer"]
    assert add_more.CODE_SEARCH_OFF_NOTE in ctx.notes
    assert not plain_words.check(add_more.CODE_SEARCH_OFF_NOTE)
    outside_switches.set_switch("code_search", True, by="owner")
    ctx2 = add_more.AddContext(words="yarn weight chart", terms=["yarn"], via="page",
                               online=True, bot_name="Sky Pal",
                               needs={"schema": "needs/v1", "jobs": []},
                               code_online=True)
    add_more._found_parts("yarn weight chart", ctx2)
    assert ctx2.searched == ["lists of apps and code on this computer and GitHub"]
    assert add_more.CODE_SEARCH_OFF_NOTE not in ctx2.notes


# ── the PIN rule ─────────────────────────────────────────────────────────────

#: Each of these made a real vault.
KEYPAD_DRESSED = ["1478963k250", "14789k63250", "123698k7410", "15935k74862",
                  "14789632501478963250", "96321478509632147850"]
#: Weak PINs the rule let through.
WEAK_PASSED = ["12-25-1990", "0611199001", "01011990xx", "19850611xx",
               "25121990ab", "January2020", "5551234567", "11235813213",
               "Minecraft1", "Liverpool1", "vaultpin1", "mysecretpin",
               "Jordan2026", "Morgan1!", "hunter2hunter2", "qwer7tyuiop",
               "(614) 555-0199", "2026jordan"]
#: Months and seasons the same way (Summer2024 was refused, January2020 not).
TIME_WORDS = ["Summer2024", "January2020", "winter2025!", "October1",
              "monday2024", "Spring2026"]
STILL_GOOD = ["Tq7-ravenQ", "k2m9x4p7Lz", "Blue-Heron-58", "mY3#vault8",
              "7fjq2kx9wp", "purple!Otter93", "Rz8qWm4t", "Sparrow&Moss4",
              "9fK2-mP7q", "8316047295", "h4ppy-Cl0ud-17", "583920174x",
              "5839201746", "Tulip-9-Harbor", "Maple-4-Lantern",
              "a-different-strong-pin", "7bS!k2@Lp", "Pass1234word",
              "4815162342", "7396304647", "5820417396"]


@pytest.mark.parametrize("pin", KEYPAD_DRESSED + WEAK_PASSED + TIME_WORDS)
def test_the_pin_rule_refuses_them(pin):
    from puffpai import plain_words, setup_ops
    ok, why = setup_ops.validate_vault_pin(pin)
    assert not ok and why and pin.lower() not in why.lower()
    assert "\u2014" not in why and not plain_words.check(why)


@pytest.mark.parametrize("pin", TIME_WORDS)
def test_months_and_seasons_get_the_same_answer(pin):
    from puffpai import zone_vault
    assert zone_vault._guessable_shape(pin) == "common"


@pytest.mark.parametrize("pin", ["1478963k250", "14789k63250", "123698k7410",
                                 "15935k74862", "14789632501478963250",
                                 "96321478509632147850"])
def test_the_vault_floor_refuses_the_keypad_shapes_too(pin):
    from puffpai.security import vault as vault_mod
    assert vault_mod.validate_vault_secret(pin)[0] is False


@pytest.mark.parametrize("pin", STILL_GOOD)
def test_good_pins_still_pass(pin):
    from puffpai import setup_ops
    assert setup_ops.validate_vault_pin(pin) == (True, "")


def test_the_floor_still_takes_a_passphrase_and_a_word_said_twice():
    from puffpai.security import vault as vault_mod
    assert vault_mod.validate_vault_secret("correct horse battery staple")[0]
    assert vault_mod.validate_vault_secret("hunter2hunter2")[0]


@pytest.mark.parametrize("pin", KEYPAD_DRESSED + ["0611199001", "Jordan2026"])
def test_the_vault_page_route_makes_no_vault_for_them(monkeypatch, tmp_path, pin):
    from puffpai import api_server
    from puffpai.security import vault as vault_mod
    monkeypatch.setattr(vault_mod, "_PBKDF2_ITERATIONS", 1000)
    monkeypatch.setattr(vault_mod, "_RECOVERY_PBKDF2_ITERATIONS", 1000)
    v = vault_mod.Vault(vault_dir=str(tmp_path / "vault"))
    monkeypatch.setattr(api_server, "_vault_instance", v)
    api_server._vault_sessions.clear()
    api_server.app.config.update(TESTING=True)
    got = api_server.app.test_client().post(
        "/vault/unlock", json={"password": pin, "initialize": True},
        headers={"Authorization": f"Bearer {api_server._API_KEY}"})
    api_server._vault_sessions.clear()
    assert got.status_code == 400
    assert not v.is_initialized


def _browser_refusals(pins):
    import shutil
    import subprocess
    if shutil.which("node") is None:
        pytest.skip("node is not installed")
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ts = os.path.join(repo, "dashboard", "app", "_lib", "vaultPin.ts")
    url = "file:///" + ts.replace("\\", "/").lstrip("/")
    script = (f"const m = await import({json.dumps(url)});\n"
              "const input = JSON.parse(process.argv[1]);\n"
              "console.log(JSON.stringify(input.map((p) => m.pinRefusal(p))));\n")
    out = subprocess.run(["node", "--input-type=module", "-e", script,
                          json.dumps(pins)], capture_output=True, text=True,
                         encoding="utf-8", timeout=60, check=True).stdout
    return json.loads(out.strip().splitlines()[-1])


def test_the_browser_rule_is_the_same_rule():
    from puffpai import setup_ops
    pins = KEYPAD_DRESSED + WEAK_PASSED + TIME_WORDS + STILL_GOOD
    browser = _browser_refusals(pins)
    differ = [p for p, why in zip(pins, browser)
              if bool(why) == setup_ops.validate_vault_pin(p)[0]]
    assert differ == []
