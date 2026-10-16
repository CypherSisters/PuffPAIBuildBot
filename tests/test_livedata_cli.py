"""``puffpai apps drafts|install|allow|deny|feed``: plain words, honest exits."""

from __future__ import annotations

import pytest

from livedata_support import (FakeResponse, candidate, fake_network,
                              fixture_probe, jargon_in, sample_bytes,
                              set_posture)
from puffpai import cli, feed_generator
from puffpai.security import egress_doors


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv(egress_doors.APPS_KILL_ENV, raising=False)
    egress_doors.clear_app_doors()
    egress_doors.reset_cache()
    yield
    egress_doors.clear_app_doors()
    egress_doors.reset_cache()


def _run(capsys, *argv) -> tuple[int, str]:
    code = cli.main(["apps", *argv])
    return code, capsys.readouterr().out


def test_the_whole_owner_path_in_the_terminal(capsys, monkeypatch):
    set_posture("enumerated")
    cand = candidate("nhtsa_recalls")
    draft = feed_generator.draft_feed_app(cand, build_online=True,
                                          probe=fixture_probe(sample_bytes(cand)))
    code, out = _run(capsys, "drafts")
    assert code == 0 and draft["id"] in out and "right website" in out
    # Checklist lines use the dashboard's plain words, never the raw values.
    assert "OK       right website" in out
    assert not any(f" {raw} " in out for raw in ("pass", "fail", "unknown"))
    assert not jargon_in(out)
    code, out = _run(capsys, "install", draft["id"])
    assert code == 0 and "will not fetch anything until you allow it" in out
    code, out = _run(capsys, "feed", draft["id"])
    assert code == 1 and "has not been allowed" in out
    code, out = _run(capsys, "allow", draft["id"])
    assert code == 0 and out.startswith("OK.") and "What leaves your computer" in out
    net = fake_network(monkeypatch)
    net.on(cand["sample_endpoint"], FakeResponse(200, sample_bytes(cand)))
    code, out = _run(capsys, "feed", draft["id"])
    assert code == 0 and "POWER TRAIN" in out and "Data from NHTSA" in out
    code, out = _run(capsys, "deny", draft["id"])
    assert code == 0 and out.startswith("Stopped.")


def test_a_yes_on_a_bot_set_to_stay_offline_is_honest(capsys):
    set_posture("island")
    cand = candidate("pbdb_taxa")
    draft = feed_generator.draft_feed_app(cand, build_online=True,
                                          probe=fixture_probe(sample_bytes(cand)))
    _run(capsys, "install", draft["id"])
    code, out = _run(capsys, "allow", draft["id"])
    assert code == 1 and "Your yes is saved" in out and "stay off the internet" in out
    assert f"To change that, run:  puffpai services on {draft['id']}" in out
    assert not jargon_in(out)


def test_missing_names_and_unknown_apps(capsys):
    code, out = _run(capsys, "allow")
    assert code == 2 and "Which app?" in out
    code, out = _run(capsys, "allow", "nothere")
    assert code == 1 and "No installed app" in out
    code, out = _run(capsys, "drafts")
    assert code == 0 and "No live-data apps are waiting" in out
