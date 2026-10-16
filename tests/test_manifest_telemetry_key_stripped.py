"""A bare ``telemetry`` key must not survive ``sanitize_manifest``.

The forbidden set already caught ``telemetry_enabled``, ``enable_telemetry``,
``phone_home`` and ``telemetry_endpoint``. It did not catch the plain word, so
a hand-edited or imported manifest could carry a ``telemetry`` block that sat
in the file looking like configuration this build reads. It never was read —
the finding is not exploitable — but a manifest is a document an owner and an
auditor read, and it must not be able to *imply* a setting that does not exist.

Zero telemetry is the first standing contract in SECURITY.md. The manifest's
own ``contracts`` block states it, and that block is regenerated verbatim on
every sanitize, which is why stripping the word elsewhere costs nothing.
"""

from __future__ import annotations

import pytest

from puffpai import build_manifest


def _manifest_with(**extra):
    m = build_manifest.assemble_manifest({"schema": "hardware_census/v1"})
    m.update(extra)
    return m


def test_bare_telemetry_key_is_stripped() -> None:
    dirty = _manifest_with(telemetry={"endpoint": "https://collect.example.com",
                                      "enabled": True})
    clean = build_manifest.sanitize_manifest(dirty)
    assert "telemetry" not in clean


def test_bare_telemetry_key_is_stripped_at_any_depth() -> None:
    """Nesting it is the obvious next attempt, so the strip is recursive."""
    dirty = _manifest_with(
        provenance={"source": "bundle",
                    "bundle": {"name": "x", "telemetry": {"enabled": True}}},
        knowledge={"packs": [{"id": "p1", "telemetry": {"enabled": True}}]},
    )
    clean = build_manifest.sanitize_manifest(dirty)
    assert "telemetry" not in clean["provenance"]["bundle"]
    assert "telemetry" not in clean["knowledge"]["packs"][0]


def test_validate_flags_the_bare_key() -> None:
    warnings = build_manifest.validate_manifest(_manifest_with(telemetry={}))
    assert any("telemetry" in w for w in warnings), warnings


def test_the_contracts_block_still_says_telemetry_none() -> None:
    """Stripping the word must not damage the contract that uses it.

    ``contracts`` is overwritten from the canonical text on every sanitize, so
    the key is restored after the strip pass. If that order ever inverts, this
    is what notices.
    """
    clean = build_manifest.sanitize_manifest(
        _manifest_with(telemetry={"enabled": True}))
    assert clean["contracts"]["telemetry"].startswith("None.")


def test_a_clean_manifest_produces_no_telemetry_warning() -> None:
    """The regression risk of this change, checked directly.

    Every legitimate manifest contains ``contracts.telemetry``. Adding the bare
    word to the forbidden set without excusing that block would have made
    ``validate_manifest`` warn about every valid manifest — noise that trains
    people to ignore the warnings that matter.
    """
    warnings = build_manifest.validate_manifest(
        build_manifest.assemble_manifest({"schema": "hardware_census/v1"}))
    assert not [w for w in warnings if "telemetry" in w], warnings


def test_write_then_read_round_trip_drops_it(tmp_path) -> None:
    """The backstop is on disk, not only in the flow."""
    target = tmp_path / "build_manifest.json"
    build_manifest.write_manifest(_manifest_with(telemetry={"enabled": True}),
                                  str(target))
    reloaded = build_manifest.read_manifest(str(target))
    assert reloaded is not None
    assert "telemetry" not in reloaded


@pytest.mark.parametrize("key", [
    "telemetry", "telemetry_enabled", "enable_telemetry", "telemetry_endpoint",
    "phone_home", "phone_home_url",
])
def test_the_whole_telemetry_vocabulary_is_forbidden(key: str) -> None:
    clean = build_manifest.sanitize_manifest(_manifest_with(**{key: True}))
    assert key not in clean
