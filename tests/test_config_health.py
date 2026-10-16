"""Config health — absent is fine, corrupt is never silent.

The trap these tests exist to prevent: a stray quote in a shipped config, a
loader that swallows the JSONDecodeError, and a wizard that cheerfully builds
someone a bot from a two-item fallback list without saying a word.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

import json
import os

import pytest

from puffpai import (
    build_interview,
    build_manifest,
    config_health,
    model_catalog,
    model_profile,
    rulepacks,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_DIR = os.path.join(REPO_ROOT, "config")


def _write(path, text):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return str(path)


# ---------------------------------------------------------------------------
# the record itself
# ---------------------------------------------------------------------------

def test_absent_is_not_degradation():
    config_health.record("thing", "/nowhere/thing.json", config_health.ABSENT, "Not installed.")
    assert config_health.is_degraded() is False
    assert config_health.summary_lines() == []
    assert config_health.manifest_block() is None
    assert [r["kind"] for r in config_health.report()] == [config_health.ABSENT]


def test_corrupt_is_degradation_and_deduplicates():
    config_health.record("thing", "/x/thing.json", config_health.UNPARSEABLE, "Bad JSON.")
    config_health.record("thing", "/x/thing.json", config_health.UNPARSEABLE, "Bad JSON again.")
    assert config_health.is_degraded() is True
    assert len(config_health.report(degraded_only=True)) == 1


def test_summary_lines_are_ascii_and_plain():
    config_health.record(
        "capability_catalog", os.path.join(CONFIG_DIR, "capability_catalog.json"),
        config_health.UNPARSEABLE, "JSON parse error.", reduced=True,
    )
    lines = config_health.summary_lines()
    assert len(lines) == 1
    line = lines[0]
    line.encode("ascii")  # raises if a non-ASCII character crept in
    assert "capability catalog" in line
    assert "missing options" in line
    assert "config/capability_catalog.json" in line


def test_clear_forgets_everything():
    config_health.record("thing", "/x/thing.json", config_health.INVALID, "Nope.")
    config_health.clear()
    assert config_health.report() == []
    assert config_health.is_degraded() is False


# ---------------------------------------------------------------------------
# the shipped configs are healthy
# ---------------------------------------------------------------------------

def test_shipped_configs_record_nothing():
    """The configs we ship must pass their own validators. This is the canary."""
    build_interview.load_catalog(os.path.join(CONFIG_DIR, "capability_catalog.json"))
    model_catalog.load_catalog(os.path.join(CONFIG_DIR, "model_catalog.json"))
    model_profile.load_profiles(os.path.join(CONFIG_DIR, "model_profiles.json"))
    rulepacks.load_packs(os.path.join(CONFIG_DIR, "rulepacks"))
    assert config_health.report() == []
    assert config_health.is_degraded() is False


# ---------------------------------------------------------------------------
# capability catalog
# ---------------------------------------------------------------------------

def test_absent_catalog_falls_back_silently(tmp_path):
    missing = str(tmp_path / "not_here.json")
    catalog = build_interview.load_catalog(missing)
    assert catalog is build_interview._FALLBACK_CATALOG
    assert config_health.is_degraded() is False
    assert config_health.summary_lines() == []


def test_corrupt_catalog_falls_back_and_records(tmp_path):
    """The exact live incident: a stray quote in capability_catalog.json."""
    real = json.load(open(os.path.join(CONFIG_DIR, "capability_catalog.json"), encoding="utf-8"))
    broken = _write(tmp_path / "catalog.json", json.dumps(real) + '"')

    catalog = build_interview.load_catalog(broken)

    assert catalog is build_interview._FALLBACK_CATALOG
    assert len(catalog["items"]) < len(real["items"])
    assert config_health.is_degraded() is True
    rec = config_health.report(degraded_only=True)[0]
    assert rec["config"] == "capability_catalog"
    assert rec["kind"] == config_health.UNPARSEABLE
    assert rec["reduced"] is True


def test_catalog_with_surface_outside_taxonomy_is_invalid(tmp_path):
    data = {
        "_surface_taxonomy": {"house.lights": {"label": "Lights", "sensitive": False}},
        "items": [{"id": "app:x", "label": "X", "surfaces": ["house.lights", "made.up"]}],
    }
    path = _write(tmp_path / "catalog.json", json.dumps(data))

    catalog = build_interview.load_catalog(path)

    assert catalog is build_interview._FALLBACK_CATALOG
    rec = config_health.report(degraded_only=True)[0]
    assert rec["kind"] == config_health.INVALID
    assert "made.up" in rec["detail"]


def test_catalog_with_empty_items_is_invalid(tmp_path):
    path = _write(tmp_path / "catalog.json", json.dumps({"items": []}))
    assert build_interview.load_catalog(path) is build_interview._FALLBACK_CATALOG
    assert config_health.is_degraded() is True


# ---------------------------------------------------------------------------
# model catalog and profiles
# ---------------------------------------------------------------------------

def test_absent_model_catalog_is_silent(tmp_path):
    assert model_catalog.load_catalog(str(tmp_path / "gone.json")) is model_catalog._FALLBACK_CATALOG
    assert config_health.is_degraded() is False


def test_model_catalog_missing_ram_field_is_invalid(tmp_path):
    data = {"models": [{"id": "m", "label": "M", "min_ram_gb": 8}]}
    path = _write(tmp_path / "mc.json", json.dumps(data))
    assert model_catalog.load_catalog(path) is model_catalog._FALLBACK_CATALOG
    rec = config_health.report(degraded_only=True)[0]
    assert rec["config"] == "model_catalog"
    assert "rec_ram_gb" in rec["detail"]


def test_absent_profiles_is_silent(tmp_path):
    assert model_profile.load_profiles(str(tmp_path / "gone.json")) is model_profile._FALLBACK_PROFILES
    assert config_health.is_degraded() is False


def test_profiles_without_default_is_invalid(tmp_path):
    path = _write(tmp_path / "p.json", json.dumps({"profiles": {"qwen2.5:7b-instruct": {}}}))
    assert model_profile.load_profiles(path) is model_profile._FALLBACK_PROFILES
    rec = config_health.report(degraded_only=True)[0]
    assert rec["config"] == "model_profiles"
    assert "_default" in rec["detail"]


def test_profiles_not_covering_the_ladder_is_invalid(tmp_path):
    path = _write(tmp_path / "p.json", json.dumps({"profiles": {"_default": {}}}))
    assert model_profile.load_profiles(path) is model_profile._FALLBACK_PROFILES
    rec = config_health.report(degraded_only=True)[0]
    assert "does not cover" in rec["detail"]


def test_profiles_coverage_does_not_cascade_from_a_broken_ladder():
    """A corrupt ladder must not also be reported as a broken profile table."""
    config_health.record(
        "model_catalog", "/x/model_catalog.json", config_health.UNPARSEABLE, "Bad JSON.",
    )
    assert model_profile.validate_profiles({"profiles": {"_default": {}}}) is None


# ---------------------------------------------------------------------------
# rulepacks
# ---------------------------------------------------------------------------

def test_absent_rulepack_dir_is_silent(tmp_path):
    packs = rulepacks.load_packs(str(tmp_path / "no_such_dir"))
    assert packs
    assert config_health.is_degraded() is False


def test_one_corrupt_rulepack_shrinks_the_vocabulary_loudly(tmp_path):
    good = {
        "surface": "house", "title": "The house", "examples": ["turn on the lights"],
        "keywords": ["lights"],
    }
    _write(tmp_path / "house.json", json.dumps(good))
    _write(tmp_path / "broken.json", '{"surface": "media",')

    packs = rulepacks.load_packs(str(tmp_path))

    assert [p["surface"] for p in packs] == ["house"]
    rec = config_health.report(degraded_only=True)[0]
    assert rec["config"] == "rulepacks"
    assert rec["kind"] == config_health.UNPARSEABLE
    assert rec["reduced"] is True


def test_rulepack_missing_owner_facing_fields_is_invalid_but_still_loads(tmp_path):
    _write(tmp_path / "house.json", json.dumps({"surface": "house", "keywords": ["lights"]}))

    packs = rulepacks.load_packs(str(tmp_path))

    assert [p["surface"] for p in packs] == ["house"]  # behaviour unchanged
    rec = config_health.report(degraded_only=True)[0]
    assert rec["kind"] == config_health.INVALID
    assert "title" in rec["detail"]


# ---------------------------------------------------------------------------
# the manifest carries the truth
# ---------------------------------------------------------------------------

CENSUS = {"hardware_class": "workstation", "memory": {"total_gb": 32}}


def test_healthy_build_carries_no_config_health_block():
    manifest = build_manifest.assemble_manifest(CENSUS)
    assert "config_health" not in manifest
    assert "config_health" not in build_manifest.sanitize_manifest(manifest)


def test_degraded_build_records_it_in_the_manifest(tmp_path):
    broken = _write(tmp_path / "catalog.json", '{"items": [},')
    build_interview.load_catalog(broken)

    manifest = build_manifest.assemble_manifest(CENSUS)

    block = manifest["config_health"]
    assert block["degraded"] is True
    assert block["configs"] == ["capability_catalog"]
    assert block["reduced"] is True
    assert block["events"][0]["file"].endswith("catalog.json")
    # No absolute path, so publishing a manifest does not leak a home directory.
    assert str(tmp_path) not in json.dumps(block)


def test_degraded_manifest_says_so_in_plain_words(tmp_path):
    build_interview.load_catalog(_write(tmp_path / "c.json", "{oops"))
    manifest = build_manifest.assemble_manifest(CENSUS)
    lines = build_manifest.manifest_summary_lines(manifest)
    heads_up = [ln for ln in lines if ln.startswith("Heads up:")]
    assert len(heads_up) == 1
    heads_up[0].encode("ascii")
    assert "capability catalog" in heads_up[0]


def test_manifest_block_survives_the_contract_guard(tmp_path):
    build_interview.load_catalog(_write(tmp_path / "c.json", "{oops"))
    manifest = build_manifest.assemble_manifest(CENSUS)

    assert build_manifest.validate_manifest(manifest) == []
    clean = build_manifest.sanitize_manifest(manifest)
    assert clean["config_health"]["degraded"] is True


def test_written_manifest_carries_the_degradation(tmp_path):
    build_interview.load_catalog(_write(tmp_path / "c.json", "{oops"))
    out = str(tmp_path / "build_manifest.json")

    build_manifest.write_manifest(build_manifest.assemble_manifest(CENSUS), out)

    written = json.load(open(out, encoding="utf-8"))
    assert written["config_health"]["configs"] == ["capability_catalog"]


# ---------------------------------------------------------------------------
# forgery
# ---------------------------------------------------------------------------

def test_imported_manifest_cannot_claim_health_it_does_not_have(tmp_path):
    """A bundle says 'all clean' while this box is running on a stub catalog."""
    build_interview.load_catalog(_write(tmp_path / "c.json", "{oops"))

    forged = build_manifest.assemble_manifest(CENSUS)
    forged["config_health"] = {"degraded": False, "configs": [], "events": []}

    clean = build_manifest.sanitize_manifest(forged)

    assert clean["config_health"]["degraded"] is True
    assert clean["config_health"]["configs"] == ["capability_catalog"]


def test_imported_manifest_cannot_invent_a_degradation():
    forged = build_manifest.assemble_manifest(CENSUS)
    forged["config_health"] = {"degraded": True, "configs": ["vault"], "events": []}
    assert "config_health" not in build_manifest.sanitize_manifest(forged)


def test_forged_health_block_cannot_smuggle_a_contract_switch(tmp_path):
    build_interview.load_catalog(_write(tmp_path / "c.json", "{oops"))
    forged = build_manifest.assemble_manifest(CENSUS)
    forged["config_health"] = {
        "degraded": True,
        "events": [{"config": "x", "disable_vault": True, "vault_pin": "1234"}],
    }

    clean = build_manifest.sanitize_manifest(forged)

    blob = json.dumps(clean)
    assert "disable_vault" not in blob
    assert "1234" not in blob
    assert clean["config_health"]["configs"] == ["capability_catalog"]


@pytest.mark.parametrize("key", ["disable_vault", "vault_pin", "phone_home"])
def test_stripped_keys_still_stripped_alongside_the_new_block(key, tmp_path):
    build_interview.load_catalog(_write(tmp_path / "c.json", "{oops"))
    manifest = build_manifest.assemble_manifest(CENSUS)
    manifest[key] = True
    assert key not in build_manifest.sanitize_manifest(manifest)
