"""One consent ticket: single use, ten minutes, this device, this action."""

from __future__ import annotations

import pytest

from puffpai.security import consent_tickets as ct

A = "device:" + "a" * 32
B = "device:" + "b" * 32


@pytest.fixture(autouse=True)
def _fresh():
    ct.reset()
    yield
    ct.reset()


def _mint(device=A, action="outside_on", target="weather", now=None):
    return ct.mint(action, target, "Switch on Weather forecast.", device, now=now)


def test_the_right_device_and_action_redeems_once():
    ticket = _mint()
    assert ct.redeem(ticket, A, "outside_on", "weather") == (True, "ok")
    assert ct.redeem(ticket, A, "outside_on", "weather") == (False, "unknown")


def test_another_device_cannot_use_it_and_it_is_burned():
    ticket = _mint(device=A)
    assert ct.redeem(ticket, B, "outside_on", "weather") == (False, "wrong_device")
    assert ct.redeem(ticket, A, "outside_on", "weather") == (False, "unknown")


def test_a_ticket_for_one_switch_does_not_open_another():
    ticket = _mint(target="weather")
    assert ct.redeem(ticket, A, "outside_on", "geocode") == (False, "mismatch")
    assert ct.redeem(ticket, A, "outside_on", "weather") == (False, "unknown")


def test_it_expires_after_ten_minutes():
    ticket = _mint(now=1000.0)
    assert ct.redeem(ticket, A, "outside_on", "weather",
                     now=1000.0 + ct.TTL_SECONDS + 1) == (False, "expired")


def test_forged_and_missing_tickets_are_refused():
    assert ct.redeem("", A, "outside_on", "weather") == (False, "absent")
    assert ct.redeem(None, A, "outside_on", "weather") == (False, "absent")
    assert ct.redeem("x" * 43, A, "outside_on", "weather") == (False, "unknown")


@pytest.mark.parametrize("device", ["", "anon:abcdef0123456789", "peer:127.0.0.1",
                                    "device:short", None])
def test_an_unpaired_caller_cannot_mint(device):
    with pytest.raises(ct.TicketRefused) as exc:
        ct.mint("outside_on", "weather", "Switch on Weather forecast.", device)
    assert exc.value.code == "not_paired"


def test_only_known_actions_and_clean_targets_mint():
    with pytest.raises(ct.TicketRefused):
        ct.mint("open_everything", "weather", "x", A)
    with pytest.raises(ct.TicketRefused):
        ct.mint("outside_on", "../etc", "x", A)
    with pytest.raises(ct.TicketRefused):
        ct.mint("outside_on", "weather", "  ", A)


def test_the_terminal_gets_its_own_process_id():
    tid = ct.terminal_device_id()
    assert ct.usable_device(tid) and tid.startswith("terminal:")
    assert ct.terminal_device_id() == tid


def test_a_flood_evicts_the_oldest_and_is_throttled():
    first = _mint(device=A, now=0.0)
    for i in range(ct.MINTS_PER_MINUTE - 1):
        _mint(device=A, now=1.0 + i)
    with pytest.raises(ct.TicketRefused) as exc:
        _mint(device=A, now=30.0)
    assert exc.value.code == "throttled"
    devices = [f"device:{i:032d}" for i in range(ct.MAX_LIVE)]
    for d in devices:
        _mint(device=d, now=100.0)
    assert ct.redeem(first, A, "outside_on", "weather", now=101.0) == (False, "unknown")


def test_the_secret_is_never_written_to_a_log(caplog):
    caplog.set_level("DEBUG")
    ticket = _mint()
    ct.redeem(ticket, B, "outside_on", "weather")
    assert ticket not in caplog.text


def test_refusals_have_plain_words():
    for verdict in ("absent", "unknown", "expired", "wrong_device", "mismatch"):
        words = ct.refusal_words(verdict)
        assert words.startswith("Nothing was switched on")
        assert "ticket" not in words.lower()
