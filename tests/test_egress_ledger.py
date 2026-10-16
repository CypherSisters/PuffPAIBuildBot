"""The outside-traffic log: one sanitised line per request, never a query."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from puffpai.security import egress_ledger


@pytest.fixture
def log(tmp_path):
    path = tmp_path / "logs" / "outside.jsonl"
    egress_ledger.set_path_override(str(path))
    yield path
    egress_ledger.set_path_override(None)


def test_the_log_lives_under_the_state_dir_by_default(tmp_path, monkeypatch):
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(tmp_path))
    assert egress_ledger.log_path() == os.path.join(str(tmp_path), "logs",
                                                    "outside.jsonl")


def test_a_line_has_exactly_the_promised_fields(log):
    assert egress_ledger.record("weather", host="api.open-meteo.com",
                                outcome="done", path="/v1/forecast",
                                status=200, bytes_in=2100, bytes_out=310,
                                what="forecast")
    rec = json.loads(log.read_text(encoding="utf-8"))
    assert set(rec) == {"t", "service", "host", "path", "method", "outcome",
                        "code", "status", "bytes_in", "bytes_out", "what"}
    assert rec["status"] == "2xx" and rec["what"] == "forecast"


def test_query_fragment_and_userinfo_never_reach_the_file(log):
    url = "https://user:pw@api.example.org/v1/x?town=Dayton&key=SECRET#frag"
    egress_ledger.record("weather", host=url, outcome="done",
                         path=egress_ledger.safe_path(url), what="forecast")
    # A caller that hands a query in the path still loses it.
    egress_ledger.record("weather", host="api.example.org", outcome="done",
                         path="/v1/x?town=Dayton", what="forecast")
    text = log.read_text(encoding="utf-8")
    for leaked in ("Dayton", "SECRET", "frag", "user", "pw", "?", "#"):
        assert leaked not in text, leaked
    assert "api.example.org" in text


def test_free_text_cannot_ride_in_what_or_code(log):
    egress_ledger.record("weather", host="api.open-meteo.com", outcome="refused",
                         code="my son Jake is allergic", what="Jake's town")
    rec = json.loads(log.read_text(encoding="utf-8"))
    assert rec["code"] == "not_allowed" and rec["what"] == "request"
    assert "Jake" not in log.read_text(encoding="utf-8")


def test_unusable_service_or_outcome_writes_nothing(log):
    assert not egress_ledger.record("Weather Card!", host="x.org", outcome="done")
    assert not egress_ledger.record("weather", host="x.org", outcome="maybe")
    assert not log.exists()


def test_read_is_newest_first_within_the_window(log):
    now = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)
    for hours_ago, sid in ((30, "old"), (2, "b"), (1, "c")):
        egress_ledger.append(egress_ledger.build_record(
            sid, host="x.org", outcome="done",
            now=now - timedelta(hours=hours_ago)))
    got = [r["service"] for r in egress_ledger.read(24, now=now)]
    assert got == ["c", "b"]


def test_a_hand_edited_line_is_resanitised_on_read(log):
    log.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    bad = {"t": now.isoformat(), "service": "weather", "host": "x.org",
           "path": "/a?town=Dayton", "outcome": "done", "what": "Dayton",
           "code": "free text", "status": "200 OK Dayton"}
    log.write_text(json.dumps(bad) + "\n", encoding="utf-8")
    (rec,) = egress_ledger.read(24)
    assert "Dayton" not in json.dumps(rec)


def test_lines_older_than_30_days_are_pruned(log, monkeypatch):
    log.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    lines = [egress_ledger.build_record("old", host="x.org", outcome="done",
                                        now=now - timedelta(days=31)),
             egress_ledger.build_record("keep", host="x.org", outcome="done",
                                        now=now - timedelta(days=29))]
    log.write_text("".join(json.dumps(r) + "\n" for r in lines), encoding="utf-8")
    monkeypatch.setitem(egress_ledger._state, "last_prune", 0.0)
    egress_ledger.record("new", host="x.org", outcome="done")
    services = [json.loads(x)["service"] for x in log.read_text().splitlines()]
    assert services == ["keep", "new"]


def test_the_file_never_grows_past_its_cap(log, monkeypatch):
    monkeypatch.setattr(egress_ledger, "MAX_BYTES", 4000)
    for _ in range(200):
        egress_ledger.record("weather", host="api.open-meteo.com",
                             outcome="done", what="forecast")
    assert log.stat().st_size <= 4000
    assert egress_ledger.read(24)  # the newest lines survive


def test_an_unwritable_log_never_breaks_a_request(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    egress_ledger.set_path_override(str(blocker / "sub" / "outside.jsonl"))
    try:
        assert egress_ledger.record("weather", host="x.org",
                                    outcome="done") is False
    finally:
        egress_ledger.set_path_override(None)


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits")
def test_the_log_is_created_owner_only(log):
    egress_ledger.record("weather", host="x.org", outcome="done")
    assert (log.stat().st_mode & 0o777) == 0o600
