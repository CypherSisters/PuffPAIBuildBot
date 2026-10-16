"""Terminal commands: puffpai home and puffpai routines, in plain words.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import pytest

from puffpai import cli, routines
from puffpai.security import consent_gate
from puffpai import plain_words  # noqa: E402  (the one banned list)

# plain_words allows "time zone" (everyday English) and bans the rest.


@pytest.fixture(autouse=True)
def ledger(tmp_path, monkeypatch):
    led = consent_gate.ConsentLedger(state_path=str(tmp_path / "c.json"),
                                     audit_path=str(tmp_path / "c.jsonl"))
    monkeypatch.setattr(consent_gate, "_default_ledger", led)
    routines.set_feed_reader(lambda: [{"id": "r", "name": "Recalls",
                                       "items": [{"title": "t"}]}])
    routines.set_library_picker(lambda terms: None)
    yield led
    routines.set_feed_reader(None)
    routines.set_library_picker(None)


def _run(capsys, *argv):
    code = cli.main(list(argv))
    out = capsys.readouterr().out
    assert plain_words.is_plain(out), plain_words.check(out)
    return code, out


def test_home_typed_position_sends_nothing_and_says_what_is_kept(capsys):
    code, out = _run(capsys, "home", "set", "--lat", "41.4993", "--lon", "-81.6944",
                     "--label", "Cleveland", "--timezone", "America/New_York")
    assert code == 0
    assert "Nothing was sent off this computer." in out
    assert "not your street or house" in out


def test_home_town_on_a_keep_it_local_build_offers_the_typed_way(capsys):
    code, out = _run(capsys, "home", "set", "Cleveland")
    assert code == 1
    assert "can't look towns up online" in out
    assert "puffpai home set --lat" in out


def test_routines_on_daily_update_is_the_yes(capsys, ledger):
    code, out = _run(capsys, "routines", "list")
    assert "Say yes with: puffpai routines on daily_update" in out
    code, out = _run(capsys, "routines", "on", "daily_update")
    assert code == 0 and out.startswith("Done.")
    assert ledger.status("routine:daily_update") == "approved"
    code, out = _run(capsys, "routines", "off", "daily_update")
    assert out.strip() == 'Okay, "Daily update" is off.'
    assert ledger.status("routine:daily_update") == "denied"
    code, out = _run(capsys, "routines", "list")
    assert "Daily update: every day at 8 am, switched off  (name: daily_update)" in out


def test_routines_add_reminder_and_refuse_unknown_kinds(capsys):
    code, out = _run(capsys, "routines", "add", "reminder", "--at", "21:00",
                     "--say", "Water the plants")
    assert code == 0 and "every day at 9 pm" in out
    code, out = _run(capsys, "routines", "add", "telescope")
    assert code == 2 and "You can add: watch, reminder" in out


def test_speak_on_says_plainly_when_this_computer_has_no_voice(capsys):
    code, out = _run(capsys, "routines", "speak", "on")
    assert code == 0 and out.startswith("Okay, updates will be said out loud.")
    assert routines.settings()["speak_aloud"] is True


@pytest.mark.parametrize("reading, expected", [
    ({"id": "gas", "name": "Average gas price", "value": 3.41, "unit": "dollars"},
     "puffpai routines add watch --feed gas --above 3.41"),
    ({"id": "finds", "name": "New fossil finds", "items": [{"title": "A find"}]},
     "puffpai routines add watch --feed finds"),
])
def test_the_watch_suggestion_is_a_command_that_works(capsys, reading, expected):
    routines.set_feed_reader(lambda: [{"id": "has space", "name": "x", "value": 1},
                                      dict(reading)])
    row = next(r for r in routines.ability_rows() if r["id"] == "routines.watch")
    assert row["status"] == "not_set_up"
    assert row["how_to_add"] == expected
    code, out = _run(capsys, *expected.split()[1:])
    assert code == 0 and out.startswith("Added")
    row = next(r for r in routines.ability_rows() if r["id"] == "routines.watch")
    assert row["status"] == "available" and row["how_to_add"] is None
