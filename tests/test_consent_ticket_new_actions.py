"""consent_tickets: four new actions, owner-only yeses, target shapes.

Each new action mints and redeems; every target helper produces a target that
fits the ticket shape even for the longest app id (39) and host (253); an
owner-only action is refused on a device that is not the owner's when the
build knows the owner, and allowed on any paired device when it does not.
"""

from __future__ import annotations

import hashlib
import json
import os

import pytest

from puffpai.security import consent_tickets as ct

DEVICE = "device:" + "a" * 32
OTHER = "device:" + "b" * 32
APP39 = "a" * 30 + "-" + "k3j9x2qa"          # 39 characters, records.new_id shape
HOST253 = ".".join(["a" * 63, "b" * 63, "c" * 63, "d" * 61])  # 253 characters


@pytest.fixture(autouse=True)
def _clean():
    ct.reset()
    ct.set_owner_devices_provider(None)
    yield
    ct.reset()
    ct.set_owner_devices_provider(None)


def test_the_new_actions_are_known_and_owner_only():
    new = {"app_host_on", "box_setup", "folder_give", "action_yes"}
    assert new <= ct.ACTIONS
    assert {"outside_on", "add_on", "key_on"} <= ct.ACTIONS
    assert ct.OWNER_ONLY == frozenset(new)


def _targets():
    folder = os.path.abspath(os.path.join(os.sep, "Users", "someone", "Pictures"))
    return {
        "app_host_on": ct.target_app_host(APP39, HOST253),
        "box_setup": ct.target_box("PuffPAI-sandbox-dev"),
        "folder_give": ct.target_folder(APP39, folder, "ro"),
        "action_yes": ct.target_action_yes(APP39 + "__add_guest",
                                           {"guest_name": "x" * 80, "n": [1, 2]}),
    }


@pytest.mark.parametrize("action", ["app_host_on", "box_setup", "folder_give",
                                    "action_yes"])
def test_each_new_action_mints_and_redeems(action):
    target = _targets()[action]
    ticket = ct.mint(action, target, "Say yes to this.", DEVICE)
    assert ct.redeem(ticket, DEVICE, action, target) == (True, "ok")
    assert ct.redeem(ticket, DEVICE, action, target) == (False, "unknown")


def test_every_target_fits_the_shape_at_the_longest_inputs():
    assert len(APP39) == 39 and len(HOST253) == 253
    for target in [*_targets().values(),
                   ct.target_held(APP39, "f" * 64),
                   ct.target_folder(APP39, os.path.abspath("x" * 200), "rw")]:
        assert ct._TARGET_RE.match(target), target  # noqa: SLF001
        assert len(target) <= 96


def test_the_target_shapes_are_exact():
    args = {"b": 1, "a": "é"}
    canon = json.dumps(args, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    assert ct.canonical_json(args) == canon
    want = hashlib.sha256(("act\n" + canon).encode()).hexdigest()[:40]
    assert ct.target_action_yes("act", args) == "a:" + want
    path = os.path.abspath(os.path.join(os.sep, "Some", "Folder"))
    digest = hashlib.sha256(os.path.normcase(path).encode()).hexdigest()[:24]
    assert ct.target_folder("app-1", path, "ro") == f"f:app-1:{digest}:ro"
    hdigest = hashlib.sha256(b"api.example.com").hexdigest()[:24]
    assert ct.target_app_host("app-1", "API.Example.com.") == f"h:app-1:{hdigest}"
    assert ct.target_box("PuffPAI-sandbox-dev") == "box:puffpai-sandbox-dev"
    assert ct.target_held("app-1", "ABCDEF0123456789" + "0" * 48) == \
        "held:app-1:abcdef0123456789"


def test_the_same_args_in_another_order_are_the_same_yes():
    assert ct.target_action_yes("x", {"a": 1, "b": 2}) == \
        ct.target_action_yes("x", {"b": 2, "a": 1})
    assert ct.target_action_yes("x", {"a": 1}) != ct.target_action_yes("x", {"a": 2})
    assert ct.target_action_yes("x", {"a": 1}) != ct.target_action_yes("y", {"a": 1})


@pytest.mark.parametrize("call", [
    lambda: ct.target_app_host("Bad:Id", "a.com"),
    lambda: ct.target_app_host("a" * 40, "a.com"),
    lambda: ct.target_app_host("app", "a.com/evil"),
    lambda: ct.target_app_host("app", "user@example.com"),
    lambda: ct.target_app_host("app", "a" * 254),
    lambda: ct.target_app_host("app", ""),
    lambda: ct.target_folder("app", "relative/path", "ro"),
    lambda: ct.target_folder("app", os.path.abspath("x"), "rwx"),
    lambda: ct.target_folder("app.x", os.path.abspath("x"), "ro"),
    lambda: ct.target_box("my distro"),
    lambda: ct.target_box(""),
    lambda: ct.target_held("app", "nothex!"),
    lambda: ct.target_held("app", "abc"),
    lambda: ct.target_action_yes("", {}),
    lambda: ct.target_action_yes("x", ["not", "a", "dict"]),
    lambda: ct.target_action_yes("x", {"f": object()}),
])
def test_bad_inputs_are_refused(call):
    with pytest.raises(ValueError):
        call()


def test_owner_only_refuses_another_device_when_the_owner_is_known():
    ct.set_owner_devices_provider(lambda: {DEVICE})
    target = ct.target_box("PuffPAI-sandbox-dev")
    with pytest.raises(ct.TicketRefused) as exc:
        ct.mint("box_setup", target, "Set up the locked box.", OTHER)
    assert exc.value.code == "not_owner"
    ticket = ct.mint("box_setup", target, "Set up the locked box.", DEVICE)
    assert ct.redeem(ticket, DEVICE, "box_setup", target) == (True, "ok")


def test_owner_only_allows_any_paired_device_when_there_is_no_owner():
    ct.set_owner_devices_provider(lambda: None)
    target = ct.target_box("PuffPAI-sandbox-dev")
    ticket = ct.mint("box_setup", target, "Set up the locked box.", OTHER)
    assert ct.redeem(ticket, OTHER, "box_setup", target) == (True, "ok")


def test_the_owner_list_is_checked_again_at_redeem():
    owners = {DEVICE, OTHER}
    ct.set_owner_devices_provider(lambda: set(owners))
    target = ct.target_app_host("app-1", "api.example.com")
    ticket = ct.mint("app_host_on", target, "Let it reach api.example.com.", OTHER)
    owners.discard(OTHER)
    assert ct.redeem(ticket, OTHER, "app_host_on", target) == (False, "not_owner")
    assert "owner" in ct.refusal_words("not_owner")


@pytest.mark.parametrize("answer", ["device:x", ["device:" + "a" * 32], 5])
def test_a_provider_that_answers_wrongly_refuses(answer):
    ct.set_owner_devices_provider(lambda: answer)
    with pytest.raises(ct.TicketRefused):
        ct.mint("folder_give", ct.target_folder("app-1", os.path.abspath("p"), "ro"),
                "Give it this folder.", DEVICE)


def test_a_provider_that_fails_refuses():
    def broken():
        raise RuntimeError("profiles unreadable")
    ct.set_owner_devices_provider(broken)
    with pytest.raises(ct.TicketRefused):
        ct.mint("action_yes", ct.target_action_yes("a__b", {}), "Remove it.", DEVICE)


def test_other_actions_ignore_the_owner_list():
    ct.set_owner_devices_provider(lambda: {DEVICE})
    ticket = ct.mint("add_on", "reading_helper", "Add the reading helper.", OTHER)
    assert ct.redeem(ticket, OTHER, "add_on", "reading_helper") == (True, "ok")


def test_the_terminal_is_not_let_through_unless_listed():
    ct.set_owner_devices_provider(lambda: {DEVICE})
    term = ct.terminal_device_id()
    target = ct.target_box("PuffPAI-sandbox-dev")
    with pytest.raises(ct.TicketRefused):
        ct.mint("box_setup", target, "Set up the locked box.", term)
    ct.set_owner_devices_provider(lambda: {DEVICE, term})
    ticket = ct.mint("box_setup", target, "Set up the locked box.", term)
    assert ct.redeem(ticket, term, "box_setup", target) == (True, "ok")


def test_a_provider_must_be_callable():
    with pytest.raises(TypeError):
        ct.set_owner_devices_provider("not callable")
