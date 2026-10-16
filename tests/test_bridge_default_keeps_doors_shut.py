"""The default Bridge level ("open", no Bridge chosen) is not an open door.

``security.bridge_guard`` does nothing at level ``open``: it is the
connect-time backstop, armed when the Bridge is up. What keeps the outside
services closed by default is in front of it: every named service in
``egress_doors`` is shut until the run posture is ``enumerated`` AND its own
switch is on.
"""
from __future__ import annotations

import json
import os

import pytest

from puffpai.config import get_config
from puffpai.security import bridge_guard, egress_doors, outside_switches


def _manifest_path() -> str:
    return os.path.join(get_config().CONFIG_DIR, "build_manifest.json")


def _forget() -> None:
    bridge_guard.forget_enforced()
    egress_doors.reset_cache()
    outside_switches.forget()


@pytest.fixture
def no_manifest():
    path = _manifest_path()
    kept = None
    if os.path.exists(path):
        with open(path, "rb") as fh:
            kept = fh.read()
        os.remove(path)
    _forget()
    yield path
    if kept is not None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(kept)
    elif os.path.exists(path):
        os.remove(path)
    _forget()


def test_a_fresh_install_opens_no_named_outside_service(no_manifest):
    assert bridge_guard.reach_level() == "open"
    assert not any(d.default for d in egress_doors.DOORS)
    opened = [d.id for d in egress_doors.DOORS if egress_doors.egress_allowed(d.id)[0]]
    assert opened == []


def test_at_the_default_level_a_service_opens_only_when_switched_on(no_manifest):
    os.makedirs(os.path.dirname(no_manifest), exist_ok=True)
    with open(no_manifest, "w", encoding="utf-8") as fh:
        json.dump({"posture": {"build_network": "online",
                               "run_egress": "enumerated"}}, fh)
    _forget()
    assert bridge_guard.reach_level() == "open"
    door = "weather"
    outside_switches.set_switch(door, False, by="owner")
    _forget()
    assert egress_doors.egress_allowed(door)[0] is False
    # The owner's (or the plan's) yes opens it; the default level keeps that working.
    outside_switches.set_switch(door, True, by="owner")
    _forget()
    assert egress_doors.egress_allowed(door)[0] is True
    outside_switches.set_switch(door, False, by="owner")
    _forget()
    assert egress_doors.egress_allowed(door)[0] is False
