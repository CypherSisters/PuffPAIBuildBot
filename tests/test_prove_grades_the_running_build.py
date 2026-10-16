"""Three defects, each pinned here.

**HIGH - Prove graded the wrong build.** ``puffpai prove`` ran the static half
in the shell it was typed in, so a healthy running bot was judged against the
caller's machine: "This build scaffolded no place of its own" and "it did not
say its name" printed directly above a transcript of the bot saying its name,
and a ``wizard_run.json`` landed in the caller's config dir. The running
backend is the build being proved, so both halves now run inside it and the
CLI writes nothing of its own.

**MEDIUM - "chosen and never downloaded" on a build that was never offered
anything.** A ``--skip-knowledge`` run recorded the goal-matched catalog as the
owner's choices, and the closing screen then said packs "were chosen during the
build and never downloaded". Nothing was offered, so nothing was chosen.

**LOW - a folder field that took the word "no" as a folder**, and wrote
``"zim_dir": "no"`` into the finished build's config.

Run:
    pytest tests/test_prove_grades_the_running_build.py
"""
from __future__ import annotations

import argparse
import json
import os
import tempfile

# api_server resolves paths and its key at import time, like its own tests do.
_TMP = tempfile.mkdtemp(prefix="puffpai_prove_routes_test_")
os.environ["PUFFPAI_DATA_DIR"] = _TMP
os.environ["PUFFPAI_STATE_DIR"] = os.path.join(_TMP, "state")
os.environ.setdefault("PUFFPAI_API_KEY", "ab" * 32)

import pytest  # noqa: E402

from puffpai import api_server, cli, prove, prove_live  # noqa: E402
from puffpai.config import get_config  # noqa: E402
from puffpai.knowledge import build_sources  # noqa: E402
from puffpai.wizard import cards, fixes, loader  # noqa: E402
from puffpai.wizard import runner as wizard_runner  # noqa: E402

API_KEY = api_server._API_KEY


def _auth():
    return {"Authorization": f"Bearer {API_KEY}"}


@pytest.fixture
def client():
    api_server.app.config.update(TESTING=True)
    return api_server.app.test_client()


@pytest.fixture
def server_state(tmp_path, monkeypatch):
    """The server's own state dir, so a written record is the server's."""
    monkeypatch.setenv("PUFFPAI_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(tmp_path / "state"))
    get_config(_force_reload=True)
    yield tmp_path
    get_config(_force_reload=True)


def _server_record(bot_name="Infinity", persona_id="infinity"):
    return {
        "schema": prove.SCHEMA, "static_at": "now", "persona_id": persona_id,
        "bot_name": bot_name, "verdict": prove.PASS, "live": None,
        "checks": [{"id": "id_sanity", "title": "The bot's id is its own",
                    "result": prove.PASS, "reason": f"'{persona_id}' is its own."}],
        "gaps": [fixes.gap(skill="prove", what="w", why="y", impact="i",
                           fix={"kind": "command", "id": "setup.rerun",
                                "args": {}})],
    }


def _live_record():
    return {"ran": True, "at": "now", "note": "", "checks": [],
            "transcript": [{"question": "q", "reply": "a", "verdict": "pass"}],
            "owner": prove_live.OWNER_UNPROVEN, "dashboard_url": ""}


# ---------------------------------------------------------------------------
# HIGH: the running backend is the source of truth
# ---------------------------------------------------------------------------

def test_the_static_route_runs_the_checks_on_the_server(client, server_state,
                                                        monkeypatch):
    """The static half runs HERE, and what it found is the server's record."""
    called: list[dict] = []

    def _static():
        called.append({})
        return _server_record()

    monkeypatch.setattr(prove, "static_prove", _static)
    reply = client.post("/build/prove/static", headers=_auth())
    assert reply.status_code == 200
    body = reply.get_json()
    assert called, "the server ran the static half itself"
    assert body["bot_name"] == "Infinity" and body["persona_id"] == "infinity"
    assert all(g["fix_command"] for g in body["gaps"])
    # Persisted server-side: the dashboard strip reads it through GET.
    after = client.get("/build/prove", headers=_auth()).get_json()
    assert after["ran"] is True and after["bot_name"] == "Infinity"


def test_the_static_route_takes_no_manifest_and_no_path(client, server_state,
                                                        monkeypatch):
    """It proves what is running. It is not told which build that is."""
    def _never():
        raise AssertionError("a body must be refused before anything runs")

    monkeypatch.setattr(prove, "static_prove", _never)
    for body in ({"manifest": {"persona": {"app": "apps"}}},
                 {"path": "C:/elsewhere"}, {"bot_name": "Somebody"},
                 {"record": {"verdict": "pass"}}):
        reply = client.post("/build/prove/static", headers=_auth(), json=body)
        assert reply.status_code == 400, body
        assert "takes no arguments" in reply.get_json()["error"]


def test_the_live_route_asks_its_own_loopback_and_no_one_else(client,
                                                              server_state,
                                                              monkeypatch):
    seen: dict = {}

    def _live(record, **kwargs):
        seen.update(kwargs)
        seen["record_name"] = record.get("bot_name")
        return _live_record()

    monkeypatch.setattr(prove, "static_prove", lambda: _server_record())
    monkeypatch.setattr(prove_live, "live_prove", _live)
    body = client.post("/build/prove/live", headers=_auth()).get_json()

    assert seen["ask_owner"] is None, "no browser at this end, ever"
    assert prove_live.check_local_url(seen["url"]).startswith("http://127.0.0.1")
    assert seen["record_name"] == "Infinity", \
        "the live half grades against the SERVER's name, not the caller's"
    assert body["live"]["ran"] is True
    assert body["live"]["transcript"][0]["reply"] == "a"
    # And a body is refused here too: no record is merged in from a request.
    refused = client.post("/build/prove/live", headers=_auth(),
                          json={"live": {"ran": True, "owner": "owner_confirmed"}})
    assert refused.status_code == 400


def test_the_owner_route_takes_one_enum_and_nothing_else(client, server_state,
                                                         monkeypatch):
    record = _server_record()
    record["live"] = _live_record()
    prove.write_record(record)

    hostile = [
        {"owner": "owner_confirmed", "checks": [{"id": "x", "result": "pass"}]},
        {"owner": "pwned"}, {"owner": True}, {"owner": ["owner_confirmed"]},
        {"owner": "owner_confirmed\x1b[2J"}, {"verdict": "pass"},
        {"live": {"owner": "owner_confirmed"}}, [], "owner_confirmed",
        {"owner": "owner_confirmed", "transcript": [{"reply": "x" * 5000}]},
    ]
    for body in hostile:
        reply = client.post("/build/prove/owner", headers=_auth(), json=body)
        assert reply.status_code == 400, body
    stored = prove.read_record()
    assert stored["live"]["owner"] == prove_live.OWNER_UNPROVEN
    assert stored["live"]["checks"] == []

    for answer in prove_live.OWNER_ANSWERS:
        ok = client.post("/build/prove/owner", headers=_auth(),
                         json={"owner": answer})
        assert ok.status_code == 200 and ok.get_json()["owner"] == answer
        assert prove.read_record()["live"]["owner"] == answer


def test_every_prove_route_is_behind_the_api_key(client):
    for path in ("/build/prove/static", "/build/prove/live",
                 "/build/prove/owner"):
        assert client.post(path).status_code == 401, path
    assert client.get("/build/prove").status_code == 401


def test_the_key_is_never_in_a_prove_answer(client, server_state, monkeypatch):
    monkeypatch.setattr(prove, "static_prove", lambda: _server_record())
    body = client.post("/build/prove/static", headers=_auth()).get_data(True)
    assert API_KEY not in body
    monkeypatch.setattr(prove_live, "live_prove", lambda r, **k: _live_record())
    live = client.post("/build/prove/live", headers=_auth()).get_data(True)
    assert API_KEY not in live
    assert API_KEY not in json.dumps(prove.read_record())


def test_the_cli_writes_nothing_when_the_backend_is_silent(tmp_path,
                                                           monkeypatch, capsys):
    """No backend, no build here: a sentence, a non-zero exit, and no file."""
    empty = tmp_path / "nowhere"
    empty.mkdir()
    monkeypatch.setenv("PUFFPAI_DATA_DIR", str(empty))
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(empty / "state"))
    monkeypatch.chdir(tmp_path)
    get_config(_force_reload=True)
    before = sorted(os.listdir(tmp_path))

    assert cli.main(["prove", "--url", "http://127.0.0.1:1", "--yes",
                     "--timeout", "2"]) == 2
    out = capsys.readouterr().out
    assert "not run: backend not answering" in out
    assert "no build in this folder" in out
    assert sorted(os.listdir(tmp_path)) == before
    assert not os.path.exists(prove.state_path(get_config(_force_reload=True)))


def test_a_local_fallback_says_it_is_local(tmp_path, monkeypatch, capsys):
    """With a build here and no backend, the label is the whole point."""
    monkeypatch.setenv("PUFFPAI_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(tmp_path / "state"))
    cfg = get_config(_force_reload=True)
    monkeypatch.setattr("puffpai.build_manifest.read_manifest",
                        lambda *a, **k: {"persona": {"name": "Localbot"}})

    assert cli.main(["prove", "--url", "http://127.0.0.1:1", "--yes",
                     "--timeout", "2"]) == 2
    out = capsys.readouterr().out
    assert cli.PROVE_LOCAL_LABEL in out
    assert "nothing was written down" in out
    assert not os.path.exists(prove.state_path(cfg))


# ---------------------------------------------------------------------------
# MEDIUM: nothing was offered, so nothing was chosen
# ---------------------------------------------------------------------------

SKIPPED_MANIFEST = {
    "knowledge": {"zim_dir": "", "auto_index": True,
                  "packs": ["pack:wikipedia", "pack:stackexchange_science"],
                  "sources": {"offered": [], "fetched": [], "not_fetched": [],
                              "skipped": "skipped"}},
}


def test_a_skipped_build_never_says_a_pack_was_chosen():
    status = build_sources.source_status(SKIPPED_MANIFEST)
    assert build_sources.sourcing_skipped(SKIPPED_MANIFEST) == "skipped"
    assert not [i for i in status["items"]
                if i["status"] == build_sources.STATUS_SELECTED_NOT_DOWNLOADED]

    record = prove.static_prove(manifest=SKIPPED_MANIFEST, launchers=None,
                                goal="a bot", bot_name="Infinity")
    check = next(c for c in record["checks"] if c["id"] == "sources_honest")
    assert check["result"] == prove.NOT_APPLICABLE
    assert "skipped" in check["reason"].lower()

    text = "\n".join(prove.closing_lines(record)).lower()
    assert "chosen" not in text, text
    assert "knowledge setup was skipped" in text
    assert text.count("knowledge setup was skipped") == 1
    gaps = [g for g in record["gaps"] if "knowledge setup" in g["what"].lower()]
    assert len(gaps) == 1 and gaps[0]["fix_command"] == "puffpai setup"


def test_a_build_that_was_offered_sources_still_reports_them():
    """The honest half of the same rule: a real plan still gets its gap."""
    offered = json.loads(json.dumps(SKIPPED_MANIFEST))
    offered["knowledge"]["sources"]["skipped"] = None
    record = prove.static_prove(manifest=offered, launchers=None,
                                goal="a bot", bot_name="Infinity")
    check = next(c for c in record["checks"] if c["id"] == "sources_honest")
    assert check["result"] in (prove.PASS, prove.GAP)
    assert check["result"] != prove.NOT_APPLICABLE


def test_the_closing_paragraph_makes_no_claim_on_a_skipped_run(capsys):
    cli._print_packs_not_downloaded(
        {"selected": ["pack:wikipedia"]},
        SKIPPED_MANIFEST["knowledge"]["sources"])
    out = capsys.readouterr().out
    assert "chosen during the build" not in out.lower()
    assert "packs you picked" not in out.lower()
    assert "Knowledge setup was skipped" in out
    assert "puffpai setup" in out
    assert len([line for line in out.splitlines() if line.strip()]) <= 3


def test_both_wizard_paths_record_the_skip(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PUFFPAI_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(tmp_path / "state"))
    cfg = get_config(_force_reload=True)
    args = argparse.Namespace(
        yes=True, skip_knowledge=True, skip_sourcing=False, skip_own_data=True,
        fetch_source=None, accept_source_size=False, own_data=None,
        zim_dir=None, no_auto_index=False)

    old = cli._setup_knowledge_step(args, cfg, posture={}, gates={},
                                    interview={"goal": "g",
                                               "selected": ["pack:wikipedia"]},
                                    non_interactive=True)
    assert build_sources.sourcing_skipped({"knowledge": old}) == "skipped"

    packages, _ = loader.load_builtin_packages()
    integrate = loader.load_module(packages["integrate"])
    ctx = wizard_runner.RunContext(args=args, cfg=cfg, non_interactive=True,
                                   shared={"interview": {"goal": "g",
                                                         "selected": []}},
                                   sections={}, package=packages["integrate"])
    ctx.proposal = integrate.propose(ctx)
    block = integrate.apply(ctx, {})["data"]["block"]
    assert build_sources.sourcing_skipped({"knowledge": block}) == "skipped"
    capsys.readouterr()


# ---------------------------------------------------------------------------
# LOW: a folder field takes a folder
# ---------------------------------------------------------------------------

def _folder_card():
    return cards.validate_card({
        "skill": "integrate", "section": "knowledge", "title": "Getting it in",
        "decided": "d", "status": "ready", "consequential": False,
        "estimate": {"bytes": 0, "seconds": 0, "basis": "calibrated"},
        "fields": [{"id": "zim_dir", "kind": "text",
                    "label": "Where the packs live", "current": "",
                    "max_length": 400, "path_like": True}],
    })


@pytest.mark.parametrize("answer", ["no", "n", "NO", "yes", "y", "skip",
                                    "none", "later", "default", "packs",
                                    "some folder", 'C:\\pa?cks'])
def test_a_word_is_not_a_folder(answer):
    card = _folder_card()
    with pytest.raises(cards.CardError) as exc:
        cards.validate_answers(card, {"zim_dir": answer})
    message = str(exc.value)
    assert message.startswith("'zim_dir' takes a folder path.")
    assert "Traceback" not in message


@pytest.mark.parametrize("answer", ["C:\\packs", "/data/packs", "~/packs",
                                    "./packs", "D:/knowledge/zim"])
def test_a_folder_is_a_folder(answer):
    assert cards.validate_answers(_folder_card(), {"zim_dir": answer})["zim_dir"]


def test_blank_keeps_the_proposal_and_a_refusal_is_said_once():
    """The console renderer's contract: say it, ask once more, then keep."""
    from puffpai.wizard import render_cli

    said: list[str] = []
    answers = iter(["no", ""])
    card = _folder_card()
    got = render_cli.render(card, ask=lambda prompt: next(answers, ""),
                            out=said.append)
    assert got == {}, "a refused answer never becomes a folder"
    # The refusal is said without the field's id in front of it, so it starts with a capital.
    assert any("takes a folder path" in line.lower() for line in said)


def test_the_integrate_card_declares_its_folder_field(tmp_path, monkeypatch,
                                                      capsys):
    monkeypatch.setenv("PUFFPAI_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(tmp_path / "state"))
    cfg = get_config(_force_reload=True)
    packages, _ = loader.load_builtin_packages()
    integrate = loader.load_module(packages["integrate"])
    args = argparse.Namespace(
        yes=True, skip_knowledge=False, skip_sourcing=True, skip_own_data=True,
        fetch_source=None, accept_source_size=False, own_data=None,
        zim_dir=None, no_auto_index=False)
    ctx = wizard_runner.RunContext(args=args, cfg=cfg, non_interactive=True,
                                   shared={"interview": {"goal": "g",
                                                         "selected": []}},
                                   sections={}, package=packages["integrate"])
    ctx.proposal = integrate.propose(ctx)
    card = cards.validate_card(ctx.proposal["card"])
    field = cards.field_map(card)["zim_dir"]
    assert field["path_like"] is True
    with pytest.raises(cards.CardError):
        cards.validate_answers(card, {"zim_dir": "no"})

    # And a caller that reaches apply() without the card keeps the proposal.
    applied = integrate.apply(ctx, {"zim_dir": "no"})
    assert applied["data"]["zim_dir"] != "no"
    capsys.readouterr()


def test_the_live_half_calls_back_on_the_port_really_bound(monkeypatch):
    """`serve --port N` is not written to the config; the callback must use N.

    The Host header must not be able to steer it either.
    """
    from puffpai import api_server

    with api_server.app.test_request_context(
            '/build/prove/live', method='POST',
            environ_overrides={'SERVER_PORT': '8210'},
            headers={'Host': 'example.com:9999'}):
        assert api_server._self_prove_url().endswith(':8210')
        assert 'example.com' not in api_server._self_prove_url()
    with api_server.app.test_request_context(
            '/build/prove/live', method='POST',
            environ_overrides={'SERVER_PORT': '80'}):
        stored = int(api_server.get_config().api_server.port or 8000) \
            if hasattr(api_server, 'get_config') else 8000
        assert api_server._self_prove_url().endswith(f':{stored}')
