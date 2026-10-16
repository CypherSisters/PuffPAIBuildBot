"""Tests for the owner's-own-data step of the wizard.

Two properties get pinned hard, because they are the ones a regression would
break silently:

* **The survey looks, it does not read.** Counts by type and size only - no file
  is opened, and no filename or file content escapes into the survey or the
  manifest block.
* **Consent is per location and defaults to no.** Nothing is approved that was
  not named, and a path that was never surveyed cannot be approved at all.

Run:
    pytest tests/test_data_sources.py
"""
from __future__ import annotations

import json
import os

import pytest

from puffpai import data_sources


SECRET = "TOPSECRET-account-number-4111111111111111"


@pytest.fixture
def corpus(tmp_path):
    """A small pretend Documents folder with a bit of everything."""
    root = tmp_path / "MyDocs"
    (root / "sub").mkdir(parents=True)
    (root / "node_modules").mkdir()
    (root / "letter.txt").write_text(SECRET, encoding="utf-8")
    (root / "scan.pdf").write_bytes(b"%PDF-1.4 not really")
    (root / "report.docx").write_bytes(b"docx bytes")
    (root / "archive.tar.gz").write_bytes(b"blob")
    (root / ".hidden.txt").write_text("hidden", encoding="utf-8")
    (root / "sub" / "notes.md").write_text("notes", encoding="utf-8")
    (root / "node_modules" / "junk.txt").write_text("junk", encoding="utf-8")
    return root


# ---------------------------------------------------------------------------
# suggestions are suggestions
# ---------------------------------------------------------------------------

def test_suggestions_are_offered_never_scanned():
    for entry in data_sources.suggested_locations():
        assert entry["suggestion"] is True
        assert entry["scanned"] is False
        assert "files" not in entry      # nothing was counted
        assert "counts" not in entry
        assert entry["why"].isascii()


def test_classify_drive_never_raises_and_labels_network_paths():
    assert data_sources.classify_drive("\\\\nas\\media") == data_sources.DRIVE_NETWORK
    assert data_sources.classify_drive("//nas/media") == data_sources.DRIVE_NETWORK
    for junk in ("", "   ", "?:\\nope", None):
        assert data_sources.classify_drive(junk) in {
            data_sources.DRIVE_FIXED, data_sources.DRIVE_REMOVABLE,
            data_sources.DRIVE_NETWORK, data_sources.DRIVE_UNKNOWN,
        }


def test_recorded_path_drops_the_account_name():
    inside = os.path.join(os.path.expanduser("~"), "Documents", "Taxes")
    recorded = data_sources.redact_path(inside)
    assert recorded.startswith("~")
    assert "Taxes" in recorded          # the owner's choice stays auditable
    assert os.path.basename(os.path.expanduser("~")) not in recorded


# ---------------------------------------------------------------------------
# the survey
# ---------------------------------------------------------------------------

def test_survey_counts_by_type_without_reading_contents(corpus):
    result = data_sources.survey([str(corpus)])
    location = result["locations"][0]

    assert location["surveyed"] is True
    assert location["counts"] == {"txt": 2, "pdf": 1, "docx": 1, "unknown": 1}
    assert location["files"] == 5       # hidden + node_modules excluded
    assert location["ingestible"] == 4
    assert location["bytes"] > 0
    assert result["ingestible"] == 4

    blob = json.dumps(result)
    assert SECRET not in blob           # no contents
    assert "letter.txt" not in blob     # no filenames
    assert "notes.md" not in blob


def test_survey_moves_and_indexes_nothing(corpus):
    before = sorted(p.name for p in corpus.rglob("*"))
    data_sources.survey([str(corpus)])
    assert sorted(p.name for p in corpus.rglob("*")) == before


def test_survey_reports_the_pii_and_consent_rules(corpus):
    result = data_sources.survey([str(corpus)])
    assert "vault" in result["vault_note"]
    assert "no setting to turn it off" in result["vault_note"]
    assert "until you say yes" in result["consent_note"]
    for key in ("vault_note", "consent_note", "record_note"):
        assert result[key].isascii()


def test_vanished_or_missing_path_degrades_instead_of_raising(tmp_path):
    missing = str(tmp_path / "unplugged-usb" / "Photos")
    result = data_sources.survey([missing])
    location = result["locations"][0]

    assert location["surveyed"] is False
    assert location["files"] == 0
    assert location["notes"]
    assert location["notes"][0].isascii()
    assert location["path"] in result["unreadable"]


def test_unreadable_path_degrades_instead_of_raising(corpus, monkeypatch):
    def _boom(*args, **kwargs):
        raise PermissionError("nope")

    monkeypatch.setattr(data_sources.os, "walk", _boom)
    result = data_sources.survey([str(corpus)])
    location = result["locations"][0]
    assert location["files"] == 0
    assert any("stopped partway" in note for note in location["notes"])


def test_permission_errors_inside_a_walk_are_recorded_not_raised(corpus, monkeypatch):
    real_walk = data_sources.os.walk

    def _walk_with_denial(path, onerror=None, followlinks=False):
        if onerror is not None:
            onerror(PermissionError("denied"))
        yield from real_walk(path, followlinks=followlinks)

    monkeypatch.setattr(data_sources.os, "walk", _walk_with_denial)
    location = data_sources.survey([str(corpus)])["locations"][0]
    assert location["surveyed"] is True
    assert any("refused access" in note for note in location["notes"])


def test_survey_stops_at_the_cap_and_says_so(corpus):
    location = data_sources.survey([str(corpus)], max_files=2)["locations"][0]
    assert location["truncated"] is True
    assert location["files"] == 2
    assert any("at least that many" in note for note in location["notes"])


# ---------------------------------------------------------------------------
# consent
# ---------------------------------------------------------------------------

def test_nothing_is_approved_by_default(corpus):
    surveyed = data_sources.survey([str(corpus)])
    selection = data_sources.apply_choices(surveyed)
    assert selection["approved"] == []
    assert selection["declined_count"] == 1
    assert selection["files"] == 0


def test_a_yes_is_per_location(corpus, tmp_path):
    other = tmp_path / "Other"
    other.mkdir()
    (other / "thing.txt").write_text("x", encoding="utf-8")
    surveyed = data_sources.survey([str(corpus), str(other)])

    selection = data_sources.apply_choices(surveyed, approve=[str(other)])
    assert len(selection["approved"]) == 1
    assert selection["approved"][0]["path"] == str(other)
    assert selection["declined_count"] == 1


def test_approving_an_unsurveyed_path_is_refused(corpus, tmp_path):
    surveyed = data_sources.survey([str(corpus)])
    selection = data_sources.apply_choices(surveyed,
                                           approve=[str(tmp_path / "never-seen")])
    assert selection["approved"] == []
    assert any("never surveyed" in note for note in selection["notes"])


def test_file_listing_requires_an_approved_location(corpus):
    surveyed = data_sources.survey([str(corpus)])
    approved = data_sources.apply_choices(
        surveyed, approve=[str(corpus)])["approved"][0]

    files = list(data_sources.iter_ingestible_files(approved))
    assert len(files) == 4
    assert all(os.path.isfile(path) for path in files)
    assert not any(os.path.basename(p).startswith(".") for p in files)

    # A location that was never surveyed yields nothing at all.
    assert list(data_sources.iter_ingestible_files({"path": str(corpus)})) == []


# ---------------------------------------------------------------------------
# the manifest block
# ---------------------------------------------------------------------------

def test_manifest_block_records_locations_and_counts_only(corpus):
    surveyed = data_sources.survey([str(corpus)])
    selection = data_sources.apply_choices(surveyed, approve=[str(corpus)])
    block = data_sources.sources_manifest_block(selection)

    assert block["location_count"] == 1
    entry = block["locations"][0]
    assert entry["files"] == 5
    assert entry["ingestible"] == 4
    assert entry["counts"]["txt"] == 2
    assert block["pii_routing"] == "vault"
    assert block["records"] == "locations-and-counts-only"

    blob = json.dumps(block)
    assert SECRET not in blob
    assert "letter.txt" not in blob
    assert "notes.md" not in blob
    assert "sub" not in json.dumps(entry["counts"])
    assert blob.isascii()


def test_declined_locations_leave_no_trace_in_the_manifest(corpus, tmp_path):
    private = tmp_path / "Oncology"
    private.mkdir()
    (private / "results.pdf").write_bytes(b"x")
    surveyed = data_sources.survey([str(corpus), str(private)])
    selection = data_sources.apply_choices(surveyed, approve=[str(corpus)])

    blob = json.dumps(data_sources.sources_manifest_block(selection))
    assert "Oncology" not in blob


def test_summary_lines_are_ascii_and_state_the_rules(corpus):
    surveyed = data_sources.survey([str(corpus)])
    lines = data_sources.source_summary_lines(surveyed)
    joined = "\n".join(lines)
    assert joined.isascii()
    assert "vault" in joined
    assert "until you say yes" in joined
    assert SECRET not in joined
    assert "letter.txt" not in joined


# ---------------------------------------------------------------------------
# learning the approved folders (through the upload path's PII screen)
# ---------------------------------------------------------------------------

class _Result:
    def __init__(self, error=None, pii_detected=False, vaulted=False):
        self.error, self.pii_detected, self.vaulted = error, pii_detected, vaulted


def _approved(tmp_path, names):
    from puffpai import data_sources as ds
    folder = tmp_path / "mine"
    folder.mkdir()
    for name in names:
        (folder / name).write_text("some words here", encoding="utf-8")
    surveyed = ds.survey([str(folder)])
    return ds.apply_choices(surveyed, [str(folder)]), folder


@pytest.fixture
def _data_dir(monkeypatch, tmp_path):
    from puffpai.config import get_config
    monkeypatch.setenv("PUFFPAI_DATA_DIR", str(tmp_path / "data"))
    get_config(_force_reload=True)
    yield
    get_config(_force_reload=True)


def test_learning_never_hands_over_or_deletes_the_owners_original(tmp_path, _data_dir):
    from puffpai import data_sources as ds
    selection, folder = _approved(tmp_path, ["a.txt", "b.md"])
    handed = []

    def _deleting_ingest(path, **kw):
        handed.append(path)
        os.remove(path)  # the real pipeline deletes its input after vaulting
        return _Result(vaulted=True)

    out = ds.learn_approved(selection, vault=object(), ingest=_deleting_ingest)
    assert out["learned"] == 2 and out["private_details_to_vault"] == 2
    assert sorted(os.listdir(folder)) == ["a.txt", "b.md"]
    assert all(not p.startswith(str(folder)) for p in handed)
    assert all(os.path.basename(p).startswith("own-") for p in handed)


def test_a_file_with_private_details_and_a_locked_vault_is_held_back(tmp_path, _data_dir):
    from puffpai import data_sources as ds
    selection, _folder = _approved(tmp_path, ["a.txt", "b.txt", "c.txt"])
    answers = iter([_Result(error="Unlock the vault", pii_detected=True),
                    _Result(error="could not read"), _Result()])
    out = ds.learn_approved(selection, vault=object(),
                            ingest=lambda path, **kw: next(answers))
    assert (out["held_back"], out["unreadable"], out["learned"]) == (1, 1, 1)
    text = " ".join(out["lines"])
    assert "were NOT learned" in text and "vault was locked" in text


def test_the_limit_is_stated_not_hidden(tmp_path, _data_dir):
    from puffpai import data_sources as ds
    selection, _f = _approved(tmp_path, [f"n{i}.txt" for i in range(5)])
    out = ds.learn_approved(selection, vault=object(), limit=2,
                            ingest=lambda path, **kw: _Result())
    assert out["learned"] == 2 and out["not_reached"] == 3
    assert "3 more file(s) were not read" in " ".join(out["lines"])


def test_a_folder_that_was_not_approved_is_never_read(tmp_path, _data_dir):
    from puffpai import data_sources as ds
    folder = tmp_path / "private"
    folder.mkdir()
    (folder / "x.txt").write_text("secret", encoding="utf-8")
    surveyed = ds.survey([str(folder)])
    declined = ds.apply_choices(surveyed, [])
    out = ds.learn_approved(declined, vault=object(),
                            ingest=lambda *a, **k: pytest.fail("read a declined folder"))
    assert out["learned"] == 0 and out["lines"] == []


def test_the_learn_record_carries_no_filename(tmp_path, _data_dir):
    import json as _json
    from puffpai import data_sources as ds
    selection, _f = _approved(tmp_path, ["Divorce papers.txt"])
    out = ds.learn_approved(selection, vault=object(), ingest=lambda p, **k: _Result())
    assert "Divorce" not in _json.dumps(out)
