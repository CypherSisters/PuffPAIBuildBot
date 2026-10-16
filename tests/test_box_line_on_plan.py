"""The locked box on the plan, its applier, and the box wiring. Box status and setup are faked."""

from __future__ import annotations

import types

import pytest

from puffpai.box import cli_tools, driver, plan, wiring
from puffpai.box import wording as W
from puffpai.security import consent_tickets as ct
from puffpai.wizard import proposal


def _sctx(items):
    return types.SimpleNamespace(parts={"items": items})


NEEDS = [{"id": "apps_code:npm:tool", "requires": ["box_setup"]}]


@pytest.fixture
def status(monkeypatch):
    box = {"st": driver.BoxStatus("no_box", W.NO_BOX, "set_up_box")}
    monkeypatch.setattr(driver, "status", lambda: box["st"])
    return box


def test_a_line_that_needs_the_box_brings_the_box_line(status):
    items = plan.items(_sctx(NEEDS))
    assert len(items) == 1
    line = proposal.validate_item(items[0])
    assert line["id"] == plan.ITEM_ID == "box:setup:locked-box"
    assert line["section"] == "box" and line["label"] == W.PLAN_BOX_LABEL
    assert line["detail"] == W.PLAN_BOX_DETAIL and line["default_on"] is True
    assert line["apply"] == {"kind": "box_setup", "ref": "box:setup"}
    assert line["outside_host"] == "dl-cdn.alpinelinux.org"


def test_no_box_line_without_a_line_that_needs_it_or_when_ready(status):
    assert plan.items(_sctx([{"id": "x:y", "requires": []}])) == []
    assert plan.items(types.SimpleNamespace(parts=None)) == []
    status["st"] = driver.BoxStatus("ready", W.READY)
    assert plan.items(_sctx(NEEDS)) == []


def test_without_windows_linux_feature_the_line_says_what_happens(status):
    status["st"] = driver.BoxStatus("no_wsl", W.NO_WSL, "install_wsl")
    line = plan.items(_sctx(NEEDS))[0]
    assert line["detail"].endswith(W.PLAN_BOX_WSL) and len(line["detail"]) <= 300


def test_on_other_systems_the_line_is_a_plain_note(status):
    status["st"] = driver.BoxStatus("unsupported", W.UNSUPPORTED)
    line = proposal.validate_item(plan.items(_sctx(NEEDS))[0])
    assert line["apply"]["kind"] == "none" and line["detail"] == W.UNSUPPORTED


def test_unticking_the_box_unticks_what_needs_it(status):
    rows = [dict(plan.items(_sctx(NEEDS))[0], on=True),
            {"id": "apps_code:npm:tool", "on": True, "requires": ["box_setup"],
             "apply": {"kind": "cli_tool"}}]
    forced = proposal.apply_requires(rows, {plan.ITEM_ID: False})
    assert forced == {"apps_code:npm:tool"} and rows[1]["on"] is False


# ── the applier ──────────────────────────────────────────────────────────────

def _ctx(*, approved=(), online=True, device=None):
    state = {"approved": True, "items": [dict(i, on=True) for i in approved]}
    progress: list = []
    return types.SimpleNamespace(shared={"summary": state}, online=online, device_id=device,
                                 progress=progress.append, lines=progress)


BOX_ITEM = {"id": plan.ITEM_ID, "section": "box", "label": W.PLAN_BOX_LABEL,
            "apply": {"kind": "box_setup", "ref": "box:setup"}}
TOOL_ITEM = {"id": "apps_code:pypi:csvkit", "section": "apps_code", "label": "csvkit",
             "requires": ["box_setup"], "apply": {"kind": "cli_tool", "ref": "pypi:csvkit"}}


def test_already_ready(status):
    status["st"] = driver.BoxStatus("ready", W.READY)
    got = plan.apply_box_setup(BOX_ITEM, _ctx())
    assert got["status"] == "added" and got["line"] == W.SETUP_ALREADY


def test_without_windows_linux_feature_it_waits_with_the_count(status):
    status["st"] = driver.BoxStatus("no_wsl", W.NO_WSL, "install_wsl")
    got = plan.apply_box_setup(BOX_ITEM, _ctx(approved=[BOX_ITEM, TOOL_ITEM, dict(TOOL_ITEM, id="x:2")]))
    assert got["status"] == "waiting" and got["line"] == W.WSL_NEEDED.format(n=2)
    got = plan.apply_box_setup(BOX_ITEM, _ctx(approved=[BOX_ITEM, TOOL_ITEM]))
    assert got["line"] == W.WSL_NEEDED_ONE


def test_offline_build_leaves_it_out(status):
    got = plan.apply_box_setup(BOX_ITEM, _ctx(online=False))
    assert got["status"] == "failed" and got["line"] == W.SETUP_OFFLINE


def test_the_plan_yes_sets_it_up_bound_to_the_approving_device(status, monkeypatch):
    ct.reset()
    seen: dict = {}

    def fake_setup(*, ticket, device_id, progress):
        seen["redeem"] = ct.redeem(ticket, device_id, "box_setup",
                                   ct.target_box(driver.distro_name()))
        seen["device"] = device_id
        progress(W.SETUP_DOWNLOADING)
        return driver.BoxStatus("ready", W.READY)
    monkeypatch.setattr(driver, "setup", fake_setup)
    ctx = _ctx(device="device:abcdefgh12345")
    got = plan.apply_box_setup(BOX_ITEM, ctx)
    assert got == {"status": "added", "line": W.SETUP_DONE, "lines": [W.SETUP_DONE]}
    assert seen["redeem"] == (True, "ok") and seen["device"] == "device:abcdefgh12345"
    assert ctx.lines == [W.SETUP_DOWNLOADING]
    assert ctx.shared["box_setup_result"]["state"] == "ready"


def test_a_failed_setup_says_why(status, monkeypatch):
    monkeypatch.setattr(driver, "setup", lambda **k: driver.BoxStatus(
        "no_box", W.SETUP_DOWNLOAD_FAILED, "set_up_box"))
    got = plan.apply_box_setup(BOX_ITEM, _ctx())
    assert got["status"] == "failed"
    assert got["line"] == W.SETUP_FAILED.format(message=W.SETUP_DOWNLOAD_FAILED)


def test_a_tool_before_the_box_line_sets_the_box_up_first_once(status, monkeypatch):
    calls: list = []

    def fake_setup(**k):
        calls.append(1)
        return driver.BoxStatus("ready", W.READY)
    monkeypatch.setattr(driver, "setup", fake_setup)
    ctx = _ctx(approved=[TOOL_ITEM, BOX_ITEM])
    assert plan.ensure_box_for(TOOL_ITEM, ctx).ready
    assert plan.ensure_box_for(TOOL_ITEM, ctx).ready
    assert calls == [1]


def test_a_tool_never_sets_the_box_up_when_the_box_line_was_left_out(status, monkeypatch):
    monkeypatch.setattr(driver, "setup", lambda **k: pytest.fail("set up without a yes"))
    st = plan.ensure_box_for(TOOL_ITEM, _ctx(approved=[TOOL_ITEM]))
    assert st.state == "no_box"


# ── wiring ───────────────────────────────────────────────────────────────────

def test_wiring_registers_every_part(monkeypatch):
    from puffpai import add_more, drawbridge
    from puffpai.integrate import actions
    from puffpai.wizard import factory
    got: dict = {"checkers": {}, "sources": {}, "plan": {}, "more": {}, "prov": {}}
    monkeypatch.setattr(drawbridge, "register_checker",
                        lambda kind, fn, **kw: got["checkers"].__setitem__(kind, (fn, kw)))
    monkeypatch.setattr(actions, "register_source",
                        lambda kind, lf, rf: got["sources"].__setitem__(kind, (lf, rf)))
    monkeypatch.setattr(factory, "register_plan_applier",
                        lambda kind, fn: got["plan"].__setitem__(kind, fn))
    monkeypatch.setattr(add_more, "register_applier",
                        lambda kind, fn: got["more"].__setitem__(kind, fn))
    monkeypatch.setattr(proposal, "register_provider",
                        lambda name, fn: got["prov"].__setitem__(name, fn))
    wiring._reset_for_tests()
    try:
        done = wiring.connect()
        assert wiring.connect() == done                       # safe to call again
    finally:
        wiring._reset_for_tests()
    assert got["prov"] == {"box": plan.items}
    assert got["plan"] == {"box_setup": plan.apply_box_setup, "cli_tool": cli_tools.apply_cli_tool}
    assert got["more"] == got["plan"]
    assert got["sources"] == {"cli": (cli_tools.list_actions, cli_tools.run_action)}
    assert got["checkers"]["cli_tool"][0] is cli_tools.cli_tool_rows
    assert got["checkers"]["cli_tool"][1]["block_codes"] == cli_tools.BLOCK_CODES
    assert got["checkers"]["cli_tool"][1]["hold_codes"] == cli_tools.HOLD_CODES
    assert got["checkers"]["box_setup"][0] is driver.box_setup_rows
    assert "waiting:cli_tool" in done and "drawbridge:cli_tool" in done


def test_factory_wiring_lists_the_box():
    from puffpai import factory_wiring
    assert "puffpai.box.wiring" in factory_wiring.ROUND3_WIRING


def test_outside_rows_are_empty_while_tools_have_no_internet():
    assert wiring.outside_rows() == []
