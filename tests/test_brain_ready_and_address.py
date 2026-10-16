"""Two ways this build lied about its brain.

1. **False readiness.** ``_ollama_chat`` never looked at the HTTP status, so the
   startup warm-up printed "[Startup] LLM model warmed up and ready" over a 404
   whose body said the configured model was never pulled. Every real request then
   503'd, and the log said the opposite. ``/system/status`` already told the
   three-state truth (connected / model_missing / unreachable); ``/setup/status``
   reported reachability only, which is a different question.

2. **The wrong Ollama.** A Windows Ollama can listen on 11435 (user env
   ``OLLAMA_HOST=0.0.0.0:11435``) while 127.0.0.1:11434 is a WSL relay to an EMPTY
   Ollama. Discovery took the first address that ANSWERED, which was the relay -
   an address where every prompt 404s. Answering is not being able to serve.

Both are the same doctrine: report what is there, and prefer the thing that can
actually do the job. Probes are stubbed; nothing here touches the network.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import argparse
import io
from contextlib import redirect_stdout

import pytest

from puffpai import api_server, brain_health, cli, setup_ops
from puffpai.config import get_config


class Reply:
    """The bit of a requests.Response these paths read."""

    def __init__(self, status_code, body=None, text=""):
        self.status_code = status_code
        self._body = body
        self.text = text

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


MODEL_MISSING_404 = Reply(404, {
    "error": "model 'qwen2.5:7b-instruct' not found, try pulling it first"})


# ── 1. a 404 is not readiness ──────────────────────────────────────────────

def test_a_404_from_the_chat_endpoint_is_reported_as_a_missing_model():
    problem = api_server._chat_reply_problem(MODEL_MISSING_404)

    assert problem is not None
    assert problem.startswith("LLM not ready:")
    assert api_server.OLLAMA_MODEL in problem
    assert api_server.OLLAMA_URL in problem
    assert f"ollama pull {api_server.OLLAMA_MODEL}" in problem


def test_a_200_is_left_alone_and_its_body_is_never_read():
    class Streamed(Reply):
        def json(self):
            raise AssertionError("a streaming 200 must not be consumed")

    assert api_server._chat_reply_problem(Streamed(200)) is None


def test_another_failure_says_the_status_it_got():
    problem = api_server._chat_reply_problem(Reply(500, {"error": "boom"}))
    assert problem is not None and "HTTP 500" in problem and "boom" in problem


class Now:
    """A Thread that runs on start(), so the warm-up can be asserted on."""

    def __init__(self, target=None, daemon=None, **kw):
        self._target = target

    def start(self):
        if self._target:
            self._target()


def test_the_warmup_logs_the_truth_instead_of_ready(monkeypatch, capsys):
    """The warm-up line never claims ready over a 404, end to end."""
    import requests

    monkeypatch.setattr(requests, "post", lambda *a, **kw: MODEL_MISSING_404)
    monkeypatch.setattr(api_server.threading, "Thread", Now)
    monkeypatch.setattr(api_server, "_background_services_started", False)
    monkeypatch.setattr(api_server, "_load_chat_history", lambda: None)
    monkeypatch.setattr(api_server, "_agent_team_enabled", lambda: False)
    # The routines clock is a thread that never ends; run
    # inline by ``Now`` it would never return. This test is about the warmup.
    monkeypatch.setattr(api_server, "_start_routines", lambda: None)

    # The backend would also wire its other parts
    # and start the browser-app host (with Thread run inline, its server loop
    # would never return); this test is about the warmup.
    from puffpai import factory_wiring
    monkeypatch.setattr(factory_wiring, "connect", lambda **kw: [])
    monkeypatch.setattr(api_server, "_start_round3_services", lambda: None)
    # The data-folder watcher and the app restarts are loops or long runs
    # too; run inline by ``Now`` the watcher would never return.
    monkeypatch.setattr(api_server, "_start_my_data_watcher", lambda: None)
    if hasattr(api_server, "_start_app_restarts"):
        monkeypatch.setattr(api_server, "_start_app_restarts", lambda: None)
    api_server.start_background_services()

    printed = capsys.readouterr().out
    assert "warmed up and ready" not in printed
    assert "[Startup] LLM not ready: model" in printed
    assert "is not installed at" in printed
    assert f"ollama pull {api_server.OLLAMA_MODEL}" in printed


def test_setup_status_agrees_with_system_status(monkeypatch):
    """One fact, two endpoints. "Reachable" alone was the disagreement."""
    monkeypatch.setattr(brain_health, "list_models",
                        lambda url, timeout=3: ["some-other-model:1b"])

    ollama = setup_ops.check_ollama()
    state = brain_health.probe(ollama["url"], ollama["selected"],
                               installed=["some-other-model:1b"])

    assert ollama["reachable"] is True          # the server is up...
    assert ollama["model_installed"] is False   # ...and cannot answer
    assert ollama["status"] == brain_health.MODEL_MISSING == state["status"]
    assert "ollama pull" in ollama["message"]


# ── 2. prefer an address that has models ───────────────────────────────────

#: A box with a WSL relay to an empty Ollama on the default port, and the
#: real one with four models on 11435.
HER_BOX = {
    "http://127.0.0.1:11434": [],
    "http://localhost:11434": [],
    "http://127.0.0.1:11435": ["qwen2.5:7b-instruct", "llama3.2:1b",
                               "nomic-embed-text", "qwen3:4b"],
    "http://localhost:11435": ["qwen2.5:7b-instruct", "llama3.2:1b",
                               "nomic-embed-text", "qwen3:4b"],
}


@pytest.fixture
def her_box(monkeypatch):
    monkeypatch.setenv("OLLAMA_HOST", "0.0.0.0:11435")
    monkeypatch.delenv("PUFFPAI_OLLAMA_URL", raising=False)
    monkeypatch.setattr(setup_ops, "list_models",
                        lambda url, timeout=3: HER_BOX.get(url))
    monkeypatch.setattr(brain_health, "list_models",
                        lambda url, timeout=3: HER_BOX.get(url))


def test_a_scheme_less_wildcard_ollama_host_becomes_a_loopback_hint(her_box):
    urls = brain_health.candidate_urls("http://localhost:11434")

    assert urls[0] == "http://127.0.0.1:11435", urls
    assert not any(url.startswith("http://0.0.0.0") for url in urls)


def test_an_address_with_models_beats_one_that_merely_answers(her_box):
    looked = brain_health.survey("http://localhost:11434")

    assert brain_health.best_address(looked) == "http://127.0.0.1:11435"
    assert setup_ops.find_ollama("http://localhost:11434") == \
        "http://127.0.0.1:11435"


def test_the_survey_says_what_it_found_at_each_address(her_box):
    lines = brain_health.survey_lines(
        brain_health.survey("http://localhost:11434"))

    assert any("11434" in ln and "no models installed" in ln for ln in lines)
    assert any("11435" in ln and "4 models" in ln for ln in lines)


def test_nothing_answering_anywhere_is_none(monkeypatch):
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    monkeypatch.setattr(brain_health, "list_models", lambda url, timeout=3: None)
    looked = brain_health.survey("http://localhost:11434")
    assert brain_health.best_address(looked) is None
    assert all("nothing answered" in ln
               for ln in brain_health.survey_lines(looked))


# ── the wizard step: same choice, said out loud ────────────────────────────

def _args(**kw):
    base = dict(yes=True, name=None, ollama_url=None, vault_password=None,
                skip_vault=False, goal=None, scaffold=False, lists=None)
    base.update(kw)
    return argparse.Namespace(**base)


def _run(fn, *a, **kw):
    buf = io.StringIO()
    with redirect_stdout(buf):
        result = fn(*a, **kw)
    return result, buf.getvalue()


@pytest.fixture
def scratch(tmp_path, monkeypatch):
    monkeypatch.setenv("PUFFPAI_DATA_DIR", str(tmp_path))
    get_config(_force_reload=True)
    yield tmp_path
    get_config(_force_reload=True)


def test_the_wizard_keeps_looking_past_an_empty_ollama(scratch, her_box):
    (url, models), printed = _run(
        cli._setup_ollama_url_step, _args(), get_config(),
        non_interactive=True)

    assert url == "http://127.0.0.1:11435"
    assert models == HER_BOX["http://127.0.0.1:11435"]
    assert "has no models installed yet" in printed
    assert "http://127.0.0.1:11435: answers, with 4 models" in printed
    assert "Found Ollama at http://127.0.0.1:11435, by probing." in printed


def test_a_configured_address_is_kept_even_with_no_models(scratch, monkeypatch):
    """The owner said where. An empty Ollama there is a pull, not a mistake."""
    url = "http://127.0.0.1:11499"
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    probed: list[str] = []

    def _list(address, timeout=3):
        probed.append(address)
        return []

    monkeypatch.setattr(setup_ops, "list_models", _list)
    monkeypatch.setattr(setup_ops, "ollama_survey",
                        lambda *a, **kw: pytest.fail("guessed past a "
                                                     "configured address"))

    (settled, models), printed = _run(
        cli._setup_ollama_url_step, _args(ollama_url=url), get_config(),
        non_interactive=True)

    assert settled == url and models == []
    assert probed == [url]
    assert "answers, but has no models installed yet" in printed
