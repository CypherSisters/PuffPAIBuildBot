"""The hook other code uses to add new plan item kinds.

``factory.register_plan_applier(kind, fn)``: "Looks good" hands each approved
line of that kind to ``fn(item, ctx)``; its lines show while the build runs;
an exception is a plain failure; kinds nobody registered behave as before.
"""
from __future__ import annotations

import types

import pytest

from puffpai.wizard import factory

GOAL = "A garden helper for my vegetable beds"


@pytest.fixture(autouse=True)
def _fresh_registry(monkeypatch):
    monkeypatch.setattr(factory, "_PLAN_APPLIERS", {})


def _row(item_id, kind, ref="x", label="A thing"):
    return {"id": item_id, "section": "x", "label": label,
            "apply": {"kind": kind, "ref": ref}, "outside_host": None,
            "needs_yes": True, "size_bytes": None}


def _ctx(items, *, online=True, device_id=None):
    state = {"approved": True, "items": [dict(i, on=True) for i in items],
             "non_interactive": False}
    if device_id:
        state["device_id"] = device_id
    shared = {"summary": state, "interview": {"goal": GOAL},
              "posture": {"build_network": "online" if online else "offline",
                          "run_egress": "enumerated"}}
    return types.SimpleNamespace(shared=shared, _sections={})


def test_a_registered_kind_is_called_and_its_lines_stream(monkeypatch):
    seen = {}

    def applier(item, ctx):
        seen["item"] = item
        seen["ctx"] = ctx
        ctx.progress("Getting it ...")
        # The line is already on the run record while the build runs.
        seen["during"] = list(ctx.shared["factory"]["lines"])
        ctx.progress("Checking it ...")
        return {"status": "added", "line": "Added: A thing.",
                "lines": ["Checking it ...", "It opens inside your bot."]}

    factory.register_plan_applier("code_project", applier)
    run = _ctx([_row("apps_code:x", "code_project", label="A thing")],
               device_id="dev-1")
    got = factory.apply_approved(run)
    assert seen["item"]["id"] == "apps_code:x"
    assert seen["item"]["apply"] == {"kind": "code_project", "ref": "x"}
    ctx = seen["ctx"]
    assert ctx.shared is run.shared
    assert ctx.online is True and ctx.device_id == "dev-1"
    assert seen["during"] == ["Getting it ..."]
    lines = got["shared"]["factory"]["lines"]
    assert lines == ["Getting it ...", "Checking it ...", "It opens inside your bot.",
                     "Added: A thing."]
    row = got["shared"]["factory"]["items"][0]
    assert (row["kind"], row["ok"], row["status"], row["line"]) == \
        ("code_project", True, "added", "Added: A thing.")
    assert got["gaps"] == []


def test_ctx_reports_offline_and_no_device():
    seen = {}
    factory.register_plan_applier(
        "picture_set", lambda item, ctx: seen.update(ctx=ctx) or
        {"status": "added_off", "line": "Added, switched off.", "lines": []})
    got = factory.apply_approved(_ctx([_row("pictures:x", "picture_set")], online=False))
    assert seen["ctx"].online is False and seen["ctx"].device_id is None
    assert got["shared"]["factory"]["items"][0]["ok"] is True


def test_an_exception_becomes_a_plain_failure(monkeypatch):
    def broken(item, ctx):
        ctx.progress("Getting it ...")
        raise RuntimeError("secret detail that must not show")

    factory.register_plan_applier("data_import", broken)
    got = factory.apply_approved(_ctx([_row("data:x", "data_import", label="Vendors")]))
    row = got["shared"]["factory"]["items"][0]
    assert (row["ok"], row["status"]) == (False, "failed")
    assert row["line"] == factory.APPLIER_FAILED_LINE == \
        "Something went wrong adding this. Nothing else changed."
    assert got["shared"]["factory"]["lines"] == ["Getting it ...", factory.APPLIER_FAILED_LINE]
    assert got["gaps"][0]["what"] == "Vendors was not added."
    assert "secret" not in repr(got)


@pytest.mark.parametrize("bad", [None, "added", {"status": "done"}, {"line": "x"}])
def test_an_odd_answer_is_a_failure(bad):
    factory.register_plan_applier("data_import", lambda item, ctx: bad)
    row = factory.apply_approved(_ctx([_row("data:x", "data_import")]))[
        "shared"]["factory"]["items"][0]
    assert (row["ok"], row["status"], row["line"]) == \
        (False, "failed", factory.APPLIER_FAILED_LINE)


@pytest.mark.parametrize("status", ["held", "waiting", "refused"])
def test_held_waiting_and_refused_are_not_added(status):
    factory.register_plan_applier(
        "cli_tool", lambda item, ctx: {"status": status, "line": "Waiting for your yes.",
                                       "lines": []})
    got = factory.apply_approved(_ctx([_row("box:x", "cli_tool", label="A tool")]))
    row = got["shared"]["factory"]["items"][0]
    assert (row["ok"], row["status"]) == (False, status)
    assert got["gaps"][0]["why"] == "Waiting for your yes."
    assert factory.added_lines(got["shared"]) == []


def test_an_unregistered_kind_behaves_as_before():
    got = factory.apply_approved(_ctx([_row("apps_code:x", "code_project")]))
    row = got["shared"]["factory"]["items"][0]
    assert row["ok"] is False and "status" not in row
    assert row["why"] == ("This build does not know how to add that yet. "
                          "Nothing was changed.")
    assert "lines" not in got["shared"]["factory"]


def test_registration_rules():
    fn = lambda item, ctx: {"status": "added", "line": "", "lines": []}  # noqa: E731
    for kind in ("knowledge.add", "live_feed", "routine", "theme", "key", "model",
                 "source_fetch", "none", "look_yourself", "ha_integration"):
        with pytest.raises(ValueError):
            factory.register_plan_applier(kind, fn)
    for kind in ("", "Code Project", "x" * 60, None):
        with pytest.raises(ValueError):
            factory.register_plan_applier(kind, fn)
    with pytest.raises(TypeError):
        factory.register_plan_applier("code_project", "not a function")
    factory.register_plan_applier("code_project", fn)
    factory.register_plan_applier("code_project", fn)   # the same one twice is fine
    # A module dict: registering again replaces the earlier function (a
    # reloaded module or a test fake takes its place), like add_more.
    later = lambda item, ctx: {"status": "added", "line": "Later.", "lines": []}  # noqa: E731
    factory.register_plan_applier("code_project", later)
    assert factory._PLAN_APPLIERS["code_project"] is later
    got = factory.apply_approved(_ctx([_row("apps_code:x", "code_project")]))
    assert got["shared"]["factory"]["items"][0]["line"] == "Later."


def test_a_reloaded_module_can_register_again(tmp_path, monkeypatch):
    import importlib
    import sys
    mod = tmp_path / "r3_fake_applier_mod.py"
    mod.write_text(
        "from puffpai.wizard import factory\n"
        "def apply(item, ctx):\n"
        "    return {'status': 'added', 'line': 'Added.', 'lines': []}\n"
        "factory.register_plan_applier('fake_reload_kind', apply)\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    try:
        first = importlib.import_module("r3_fake_applier_mod")
        old = factory._PLAN_APPLIERS["fake_reload_kind"]
        importlib.reload(first)                  # must not raise
        assert factory._PLAN_APPLIERS["fake_reload_kind"] is not old
        assert factory._PLAN_APPLIERS["fake_reload_kind"] is first.apply
    finally:
        sys.modules.pop("r3_fake_applier_mod", None)


def test_a_built_in_kind_is_never_handed_to_an_applier(monkeypatch):
    calls = []
    monkeypatch.setitem(factory._PLAN_APPLIERS, "routine",
                        lambda item, ctx: calls.append(item))
    monkeypatch.setattr(factory, "_add_routine",
                        lambda apply, label: {"ok": True, "line": "A routine"})
    factory.apply_approved(_ctx([_row("updates:x", "routine")]))
    assert calls == []


# -- more cases -----------------------------------------------

def test_the_applier_gets_a_deep_copy_and_her_approved_line_stays():
    def applier(item, ctx):
        item["apply"]["kind"] = "theme"
        item["apply"]["ref"] = "elsewhere"
        item["label"] = "Something else"
        return {"status": "added", "line": "Added.", "lines": []}

    factory.register_plan_applier("code_project", applier)
    run = _ctx([_row("apps_code:x", "code_project", ref="x", label="A thing")])
    got = factory.apply_approved(run)
    saved = run.shared["summary"]["items"][0]
    assert saved["apply"] == {"kind": "code_project", "ref": "x"}
    assert saved["label"] == "A thing"
    assert got["shared"]["factory"]["items"][0]["label"] == "A thing"


def test_streaming_keeps_an_existing_factory_record():
    seen = {}

    def applier(item, ctx):
        ctx.progress("Getting it ...")
        record = ctx.shared["factory"]
        seen["during"] = dict(record, lines=list(record["lines"]))
        return {"status": "added", "line": "Added.", "lines": []}

    factory.register_plan_applier("code_project", applier)
    run = _ctx([_row("apps_code:x", "code_project")])
    earlier = {"at": "earlier", "build_online": True, "items": [{"id": "a"}]}
    run.shared["factory"] = earlier
    got = factory.apply_approved(run)
    assert run.shared["factory"] is earlier           # not replaced while running
    assert seen["during"]["items"] == [{"id": "a"}]
    assert seen["during"]["at"] == "earlier"
    assert seen["during"]["lines"] == ["Getting it ..."]
    assert got["shared"]["factory"]["lines"] == ["Getting it ...", "Added."]
