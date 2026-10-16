"""The backend wiring: hooks in order, guarded parts.

* ``factory_wiring.connect`` calls the app host's and the box's wiring, then
  Toolsmith's, then closes Drawbridge's registry, in that order, and closes
  it even when a part raises;
* blueprints are registered when present, skipped when absent, and a
  part that exists but is broken raises;
* the chat step runs right after the app fastpath (never before it);
* the app host starts with the backend and stops at exit.
"""
from __future__ import annotations

import importlib.abc
import importlib.machinery
import sys
import types

import flask
import pytest

from puffpai import api_server, drawbridge, factory_wiring
from puffpai.integrate import apply
from puffpai.integrate import chat as integrate_chat


def _module(name: str, **attrs) -> types.ModuleType:
    mod = types.ModuleType(name)
    mod.__dict__.update(attrs)
    return mod


def _package(name: str) -> types.ModuleType:
    mod = _module(name)
    mod.__path__ = []  # a package, so children can be looked up
    return mod


class _BrokenFinder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Makes *target* exist but fail to import (a missing dependency)."""

    def __init__(self, target: str):
        self.target = target

    def find_spec(self, fullname, path, target=None):
        if fullname == self.target:
            return importlib.machinery.ModuleSpec(fullname, self)
        return None

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        raise ModuleNotFoundError("No module named 'a_missing_dependency'",
                                  name="a_missing_dependency")


def _hide(monkeypatch, package: str) -> None:
    """Make *package* absent, forgetting any of its modules already imported
    (on the merged build the real modules are loaded by then)."""
    for name in [n for n in sys.modules if n.startswith(package + ".")]:
        monkeypatch.delitem(sys.modules, name, raising=False)
    monkeypatch.setitem(sys.modules, package, None)


@pytest.fixture
def open_registry(monkeypatch):
    monkeypatch.setattr(drawbridge, "_CHECKERS", {})
    monkeypatch.setattr(drawbridge, "_REGISTRY", {"closed": False})
    monkeypatch.setattr(factory_wiring, "_ROUND3", {"wired": False})


def test_round3_wiring_runs_in_order_then_closes(monkeypatch, open_registry):
    from puffpai.integrate import actions as integrate_actions
    # The action sources are process-wide: start from none, as a backend does.
    monkeypatch.setattr(integrate_actions, "_SOURCES", {})
    order = []

    def web_connect():
        drawbridge.register_checker("static_web", lambda d: [])
        order.append("webapps")
        return ["webapps:wired"]
    monkeypatch.setitem(sys.modules, "puffpai.webapps", _package("puffpai.webapps"))
    monkeypatch.setitem(sys.modules, "puffpai.webapps.wiring",
                        _module("puffpai.webapps.wiring", connect=web_connect))
    monkeypatch.setitem(sys.modules, "puffpai.box", _package("puffpai.box"))
    monkeypatch.setitem(sys.modules, "puffpai.box.wiring", _module(
        "puffpai.box.wiring", connect=lambda: order.append("box") or ["box:wired"]))
    real_apply_connect = apply.connect
    monkeypatch.setattr(apply, "connect",
                        lambda: order.append("toolsmith") or real_apply_connect())
    real_close = drawbridge.close_registration
    monkeypatch.setattr(drawbridge, "close_registration",
                        lambda: order.append("close") or real_close())
    done = factory_wiring.connect()
    assert order == ["webapps", "box", "toolsmith", "close"]
    assert "webapps:wired" in done and "box:wired" in done
    assert "drawbridge:code_project" in done and done[-1] == "drawbridge:closed"
    assert "routines:feed_reader" in done          # the earlier wiring still runs
    assert drawbridge.checker_kinds() == ["code_project", "data_template", "folder_palette",
                                          "glue", "picture_set", "software_package",
                                          "static_web"]
    assert "drawbridge:software_package" in done   # puffpai.deps, before the close
    assert {"actions:data_query", "actions:palette", "add_more_remover:integration",
            "abilities:integrate", "plan_applier:folder_palette"} <= set(done)
    assert drawbridge.registration_closed()
    with pytest.raises(drawbridge.RegistrationClosed):
        drawbridge.register_checker("late_kind", lambda d: [])
    # A second connect (the terminal chat after the backend) is harmless.
    assert "drawbridge:closed" not in factory_wiring.connect()


def test_absent_parts_are_skipped(monkeypatch, open_registry):
    _hide(monkeypatch, "puffpai.webapps")
    _hide(monkeypatch, "puffpai.box")
    done = factory_wiring.connect_round3()
    assert "puffpai.webapps.wiring: not in this build yet" in done
    assert "puffpai.box.wiring: not in this build yet" in done
    assert drawbridge.registration_closed()


def test_a_broken_part_raises_and_the_registry_still_closes(monkeypatch, open_registry):
    _hide(monkeypatch, "puffpai.webapps")
    monkeypatch.setitem(sys.modules, "puffpai.webapps", _package("puffpai.webapps"))
    monkeypatch.setattr(sys, "meta_path",
                        [_BrokenFinder("puffpai.webapps.wiring")] + sys.meta_path)
    with pytest.raises(ModuleNotFoundError):
        factory_wiring.connect_round3()
    assert drawbridge.registration_closed()


def test_a_part_whose_connect_raises_still_closes_the_registry(monkeypatch, open_registry):
    def boom():
        raise RuntimeError("wiring failed")
    monkeypatch.setitem(sys.modules, "puffpai.webapps", _package("puffpai.webapps"))
    monkeypatch.setitem(sys.modules, "puffpai.webapps.wiring",
                        _module("puffpai.webapps.wiring", connect=boom))
    with pytest.raises(RuntimeError):
        factory_wiring.connect()
    assert drawbridge.registration_closed()


# -- blueprints ---------------------------------------------------------------------

def test_the_integrations_blueprint_is_on_the_backend():
    assert "integrate" in api_server.app.blueprints
    rules = {r.rule for r in api_server.app.url_map.iter_rules()}
    assert {"/integrations", "/integrations/<integration_id>",
            "/integrations/<integration_id>/decide"} <= rules


def test_blueprints_present_absent_and_broken():
    app = flask.Flask("hooks-test")
    bp = flask.Blueprint("fake_part", __name__)
    present = _module("fake.present", bp=bp)
    seen = []

    def importer(name):
        seen.append(name)
        return {"a.absent": None, "a.present": present}[name]
    assert api_server._register_round3_blueprints(
        app, modules=("a.absent", "a.present"), importer=importer) == ["a.present"]
    assert "fake_part" in app.blueprints and seen == ["a.absent", "a.present"]
    # Registering again is harmless.
    api_server._register_round3_blueprints(app, modules=("a.present",), importer=importer)

    def broken(name):
        raise ModuleNotFoundError("No module named 'dep'", name="dep")
    with pytest.raises(ModuleNotFoundError):
        api_server._register_round3_blueprints(app, modules=("a.broken",),
                                               importer=broken)
    with pytest.raises(RuntimeError):
        api_server._register_round3_blueprints(
            app, modules=("a.nobp",), importer=lambda n: _module("a.nobp"))


def test_the_real_importer_skips_absent_and_raises_on_broken(monkeypatch):
    app = flask.Flask("hooks-test-2")
    _hide(monkeypatch, "puffpai.box")       # the box modules may be loaded already
    assert api_server._register_round3_blueprints(app, modules=("puffpai.box.api",)) == []
    _hide(monkeypatch, "puffpai.webapps")
    monkeypatch.setitem(sys.modules, "puffpai.webapps", _package("puffpai.webapps"))
    monkeypatch.setattr(sys, "meta_path",
                        [_BrokenFinder("puffpai.webapps.api")] + sys.meta_path)
    with pytest.raises(ModuleNotFoundError):
        api_server._register_round3_blueprints(app, modules=("puffpai.webapps.api",))


# -- the chat hook sits right after the fastpath ---------------------------------------

@pytest.fixture
def hooks(monkeypatch):
    order = []
    state = {"fastpath": None}
    monkeypatch.setattr(api_server, "_maybe_add_more_answer",
                        lambda *a, **k: order.append("add_more") or None)
    monkeypatch.setattr(api_server, "_try_app_fastpath",
                        lambda m: order.append("fastpath") or state["fastpath"])
    monkeypatch.setattr(integrate_chat, "try_answer",
                        lambda m, *, surface, device_id: order.append(
                            ("integrate", surface, device_id)) or {"reply": "from the app"})
    return order, state


def test_text_chat_asks_the_app_step_after_the_fastpath(hooks):
    order, state = hooks
    from puffpai.security import consent_tickets
    got = api_server._process_chat_query("read the guest list", [])
    assert got["response"] == "from the app"
    assert order == ["add_more", "fastpath",
                     ("integrate", "terminal", consent_tickets.terminal_device_id())]
    order.clear()
    state["fastpath"] = "the fastpath answered"
    assert api_server._process_chat_query("add x to my list", [])["response"] \
        == "the fastpath answered"
    assert order == ["add_more", "fastpath"]


def test_voice_asks_after_the_fastpath_and_has_no_identity(hooks):
    order, _state = hooks
    api_server._process_voice_query("read the guest list")
    assert "fastpath" in order
    assert not any(isinstance(o, tuple) for o in order)   # no identity: never asked


def test_the_step_is_asked_once_per_request(hooks):
    order, _state = hooks
    app = api_server.app
    with app.test_request_context("/chat/send", method="POST",
                                  headers={"Authorization": f"Bearer {api_server._API_KEY}",
                                           "X-Puff-Device-Id": "deviceaaaa000001"},
                                  environ_base={"REMOTE_ADDR": "127.0.0.1"}):
        app.preprocess_request()
        api_server._maybe_integration_answer("read the guest list")
        assert api_server._maybe_integration_answer("read the guest list") is None
    assert sum(1 for o in order if isinstance(o, tuple)) == 1


def test_a_failing_step_never_breaks_chat(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(integrate_chat, "try_answer", boom)
    assert api_server._maybe_integration_answer("read the guest list") is None


# -- the app host starts and stops with the backend -------------------------------------

@pytest.fixture
def backend_start(monkeypatch):
    monkeypatch.setattr(api_server, "_background_services_started", False)
    monkeypatch.setattr(factory_wiring, "connect", lambda **kw: [])
    monkeypatch.setattr(api_server, "_start_routines", lambda: None)
    monkeypatch.setattr(api_server, "_start_book_learning", lambda: None)
    monkeypatch.setattr(api_server, "_start_my_data_watcher", lambda: None)
    monkeypatch.setattr(api_server, "_agent_team_enabled", lambda: False)
    monkeypatch.setattr(api_server, "_load_chat_history", lambda: None)
    monkeypatch.setattr(api_server.threading, "Thread",
                        lambda *a, **k: types.SimpleNamespace(start=lambda: None))
    exits = []
    import atexit
    monkeypatch.setattr(atexit, "register", lambda fn, *a, **k: exits.append(fn))
    yield exits


def test_the_app_host_starts_with_the_backend_and_stops_at_exit(monkeypatch, backend_start):
    calls = []
    host = _module("puffpai.webapps.host", start=lambda cfg: calls.append(("start", cfg)),
                   stop=lambda: calls.append(("stop",)))
    monkeypatch.setitem(sys.modules, "puffpai.webapps", _package("puffpai.webapps"))
    monkeypatch.setitem(sys.modules, "puffpai.webapps.host", host)
    api_server.start_background_services()
    assert calls == [("start", api_server._cfg)]
    assert backend_start == [api_server._stop_round3_services]
    backend_start[0]()
    assert calls[-1] == ("stop",)


def test_no_app_host_no_crash(monkeypatch, backend_start):
    _hide(monkeypatch, "puffpai.webapps")
    api_server.start_background_services()
    assert backend_start == []


def test_an_app_host_that_fails_to_start_does_not_stop_the_backend(monkeypatch,
                                                                    backend_start):
    def boom(cfg):
        raise OSError("port in use")
    monkeypatch.setitem(sys.modules, "puffpai.webapps", _package("puffpai.webapps"))
    monkeypatch.setitem(sys.modules, "puffpai.webapps.host",
                        _module("puffpai.webapps.host", start=boom, stop=lambda: None))
    api_server.start_background_services()
    assert backend_start == []
