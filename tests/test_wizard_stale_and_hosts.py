"""Two tables that go wrong quietly, and the tests that make them go wrong loudly.

**Staleness (spec B.4).** Every answerable field on every built-in card either
has a row in ``stale.FIELD_STALENESS`` or is on the explicit
``stale.MAKES_NOTHING_STALE`` list. A field with neither is the failure this
file exists against: the owner changes something, nothing downstream is marked
for rechecking, and the build quietly keeps a proof that is no longer about it.

**Declared hosts (spec A.2, A.6).** Integrate downloads, so its manifest has to
say from where. A static manifest cannot read the registry at load, so the
hosts are declared statically and this file fails the day the registry gains a
host the manifest does not name -- which is the day an undeclared download
would otherwise become normal.

Run:
    pytest tests/test_wizard_stale_and_hosts.py
"""
from __future__ import annotations

import copy
import json
import os
import urllib.parse

import pytest

from puffpai import hardware_census, setup_ops
from puffpai.config import get_config
from puffpai.wizard import cards, loader, runner, service, stale

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_REGISTRY = os.path.join(_ROOT, "config", "source_registry.json")


@pytest.fixture
def steady(tmp_path, monkeypatch):
    """An isolated data dir and a machine that does not move."""
    monkeypatch.setenv("PUFFPAI_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(tmp_path / "state"))
    cfg = get_config(_force_reload=True)
    census = hardware_census.run_census()
    monkeypatch.setattr(hardware_census, "run_census",
                        lambda *a, **k: copy.deepcopy(census))
    monkeypatch.setattr(setup_ops, "list_models", lambda *a, **k: ["qwen2.5:7b"])
    monkeypatch.setattr(setup_ops, "ollama_survey", lambda *a, **k: [])
    yield cfg
    get_config(_force_reload=True)


# -- staleness ----------------------------------------------------------------

def test_every_answerable_field_says_what_it_invalidates(steady):
    offered = service.propose_cards()
    assert offered["refusals"] == [], offered["refusals"]
    assert offered["cards"], "no card proposed, so nothing was checked"

    missing = []
    for card in offered["cards"]:
        for field in card["fields"]:
            if field["kind"] in cards.READ_ONLY_KINDS:
                continue
            key = f"{card['section']}.{field['id']}"
            if (key not in stale.FIELD_STALENESS
                    and key not in stale.MAKES_NOTHING_STALE):
                missing.append(f"{cards.card_id(card)} -> {key}")
    assert missing == [], (
        "these fields say nothing about what they invalidate; add a row to "
        "stale.FIELD_STALENESS or put them on MAKES_NOTHING_STALE with a "
        f"reason: {missing}")


def test_nothing_is_on_both_lists():
    both = set(stale.FIELD_STALENESS) & set(stale.MAKES_NOTHING_STALE)
    assert both == set(), f"{sorted(both)} both makes things stale and does not"


def test_every_row_names_skills_this_build_has():
    for key, skills in stale.FIELD_STALENESS.items():
        assert skills, f"{key} has an empty row; use MAKES_NOTHING_STALE"
        for skill in skills:
            assert skill in runner.SKILL_ORDER, f"{key} names '{skill}'"


def test_a_secret_never_reaches_the_table():
    """A PIN or a token is not a field anything downstream reads."""
    for key in stale.FIELD_STALENESS:
        assert not any(word in key for word in
                       ("pin", "token", "password", "secret", "recovery"))


# -- declared hosts -----------------------------------------------------------

def _registry_hosts() -> set[str]:
    with open(_REGISTRY, encoding="utf-8") as handle:
        registry = json.load(handle)
    hosts = set()
    for entry in registry.get("sources") or ():
        if entry.get("kiwix_book"):
            # A Kiwix book row reaches the library's listing host and its
            # download host; mirrors come from each book's own .meta4 file at
            # download time and cannot be declared ahead of time.
            from puffpai.catalogs import kiwix_opds
            hosts.update({kiwix_opds.SEARCH_HOST, kiwix_opds.DOWNLOAD_HOST})
        for key in ("source_url", "download_url", "url"):
            value = entry.get(key)
            if isinstance(value, str) and "://" in value:
                host = urllib.parse.urlparse(value).hostname
                if host:
                    hosts.add(host.lower())
    return hosts


def _declared(package_id: str) -> set[str]:
    packages, refusals = loader.load_builtin_packages()
    assert refusals == [], refusals
    permissions = packages[package_id].manifest.get("permissions") or {}
    return {str(host).lower()
            for host in (permissions.get("network_hosts") or ())}


def test_integrate_declares_every_host_the_registry_can_send_it_to():
    """The manifest cannot read the registry, so a test reads both.

    This is the check that fails on the day somebody adds a source from a new
    host: the download would work, the manifest would not mention it, and a
    declaration that is not true of the code is worse than none.
    """
    undeclared = sorted(_registry_hosts() - _declared("integrate"))
    assert undeclared == [], (
        "config/source_registry.json can send Integrate to hosts its "
        f"manifest does not declare: {undeclared}")


def test_integrate_declares_no_host_the_registry_never_names():
    """Least privilege in the other direction: no leftover hosts."""
    extra = sorted(_declared("integrate") - _registry_hosts())
    assert extra == [], (
        f"integrate/manifest.json declares hosts nothing can reach: {extra}")


def test_a_skill_that_downloads_does_not_claim_it_reaches_nothing():
    """The honesty rule, stated once: bytes imply a host."""
    packages, _ = loader.load_builtin_packages()
    integrate = packages["integrate"]
    assert integrate.manifest["egress"] == "build_only"
    assert (integrate.manifest["permissions"]["network_hosts"]), (
        "Integrate downloads and declared no host at all")
