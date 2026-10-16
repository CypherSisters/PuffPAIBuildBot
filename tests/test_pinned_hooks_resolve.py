"""On the merged build, every pinned hook resolves.

Design section 9 (I-1 to I-9) pins the modules other code is written
against, through optional imports (I-10): a module that is absent means "not
in this build yet". Once everything is merged, nothing may be absent any
more: this test imports every module and checks every pinned name.

Skipped unless ``PUFFPAI_R3_MERGED=1`` (set on the merged
build). On a branch with only part of the work, the other
modules are missing by design.

Run:
    PUFFPAI_R3_MERGED=1 pytest tests/test_pinned_hooks_resolve.py
"""
from __future__ import annotations

import importlib
import os

import pytest

#: module -> the names design section 9 pins on it (functions, classes,
#: constants and blueprints). A module with no names only has to import.
PINNED: dict[str, tuple[str, ...]] = {
    # I-1
    "puffpai.security.outbound_query": (
        "Sendable", "ScreenResult", "screen", "subject_sendable", "is_sendable",
        "url_quote"),
    "puffpai.catalogs.personal_words": ("PersonalWords", "find", "subject_of"),
    "puffpai.catalogs.goal_words": ("search_terms", "MAX_TERMS"),
    # I-6
    "puffpai.wizard.factory": ("register_plan_applier",),
    # I-2
    "puffpai.source_fetch": ("get_small", "fetch_small", "get_archive"),
    "puffpai.security.safe_archive": (
        "extraction_root", "check_member_name", "extract", "safe_rmtree"),
    "puffpai.security.host_programs": ("SAFE_CWD", "path", "run_kwargs"),
    "puffpai.security.egress_doors": (
        "register_web_app_door", "app_web_hosts", "set_app_web_hosts",
        "app_web_request"),
    "puffpai.outside_services": ("register_rows",),
    "puffpai.security.consent_tickets": (
        "ACTIONS", "OWNER_ONLY", "set_owner_devices_provider",
        "target_action_yes", "target_folder", "target_app_host", "target_box",
        "target_held"),
    "puffpai.security.outside_switches": ("switch_id_app", "switch_id_box"),
    "puffpai.security.egress_ledger": ("WHAT",),
    # I-3
    "puffpai.catalogs.where_to_look": (
        "registry", "route", "find_local", "find_online", "details",
        "new_budget", "license_words", "snapshot_vectors"),
    "puffpai.catalogs.trust_floor": ("check", "pretick_allowed"),
    "puffpai.catalogs.code_mode": ("guess", "CANDIDATE_TEXT_SHAPE",
                                   "candidate_text"),
    # I-4
    "puffpai.wizard.needs": ("prewarm", "run", "from_words", "choose", "sends",
                             "get_helper"),
    "puffpai.wizard.find_parts": ("plan_items", "found_for"),
    "puffpai.catalogs.meaning": ("helper_state", "score"),
    "puffpai.catalogs.page_check": ("looks_like_page",),
    # I-5
    "puffpai.wizard.needs_api": ("bp",),
    # I-7
    "puffpai.integrate.actions": ("register_source", "all_actions", "run",
                                  "undo_last"),
    "puffpai.integrate.chat": ("try_answer",),
    "puffpai.drawbridge": ("register_checker", "close_registration"),
    "puffpai.integrate.records": ("save", "get", "all", "remove", "new_id"),
    "puffpai.integrate.api": ("bp",),
    "puffpai.integrate.cli": ("add_parsers",),
    # I-8
    "puffpai.box.driver": (
        "distro_name", "status", "install_wsl", "setup", "run", "put_project",
        "export", "remove_project", "remove_box", "ingest_outside", "waiting",
        "add_waiting", "finish_waiting"),
    "puffpai.box.wiring": ("connect",),
    "puffpai.box.jail": ("bwrap_argv",),
    "puffpai.box.cli_tools": (),
    "puffpai.box.api": ("bp",),
    "puffpai.box_cli": ("add_parsers",),
    "puffpai.remove_cli": ("add_parsers",),
    "puffpai.security.owner_folders": ("check", "deny_roots", "write_folder",
                                       "after_write_sweep"),
    # I-9
    "puffpai.webapps.install": ("install_static", "uninstall"),
    "puffpai.webapps.host": ("origin_for", "start", "stop"),
    "puffpai.webapps.actions": ("list_actions", "run"),
    "puffpai.webapps.mirror": ("restore",),
    "puffpai.webapps.wiring": ("connect",),
    "puffpai.webapps.api": ("bp",),
}

#: The spec's list of modules; every one is in PINNED.
SPEC_MODULES = (
    "puffpai.security.outbound_query", "puffpai.catalogs.personal_words",
    "puffpai.wizard.needs", "puffpai.wizard.find_parts", "puffpai.catalogs.meaning",
    "puffpai.catalogs.page_check", "puffpai.catalogs.where_to_look",
    "puffpai.catalogs.trust_floor", "puffpai.catalogs.code_mode",
    "puffpai.security.safe_archive", "puffpai.security.host_programs",
    "puffpai.wizard.needs_api", "puffpai.integrate.actions", "puffpai.integrate.chat",
    "puffpai.integrate.records", "puffpai.integrate.api", "puffpai.integrate.cli",
    "puffpai.box.driver", "puffpai.box.jail", "puffpai.box.cli_tools",
    "puffpai.box.api", "puffpai.box_cli", "puffpai.remove_cli",
    "puffpai.security.owner_folders", "puffpai.webapps.install",
    "puffpai.webapps.host", "puffpai.webapps.actions", "puffpai.webapps.mirror",
    "puffpai.webapps.wiring", "puffpai.webapps.api")

#: The consent actions and ledger rows this build adds (I-2).
R3_ACTIONS = ("app_host_on", "box_setup", "folder_give", "action_yes")
R3_WHAT = ("code_search", "picture_search", "code_download", "picture_download",
           "box_install", "box_traffic", "app_traffic", "app_parts")

MERGED = os.environ.get("PUFFPAI_R3_MERGED") == "1"


def test_the_list_covers_the_spec():
    """Runs on every branch: the pinned list names every module the spec does."""
    assert set(SPEC_MODULES) <= set(PINNED)


merged_only = pytest.mark.skipif(
    not MERGED, reason="set PUFFPAI_R3_MERGED=1 on the merged build")


@merged_only
@pytest.mark.parametrize("modname", sorted(PINNED))
def test_every_pinned_module_imports_with_its_names(modname):
    module = importlib.import_module(modname)
    missing = [name for name in PINNED[modname] if not hasattr(module, name)]
    assert not missing, f"{modname} is missing {missing}"


@merged_only
def test_the_new_consent_actions_and_ledger_rows_exist():
    from puffpai.security import consent_tickets, egress_ledger
    assert set(R3_ACTIONS) <= set(consent_tickets.ACTIONS)
    assert set(R3_ACTIONS) <= set(consent_tickets.OWNER_ONLY)
    what = egress_ledger.WHAT
    assert set(R3_WHAT) <= set(what), sorted(set(R3_WHAT) - set(what))


@merged_only
@pytest.mark.parametrize("modname", ["puffpai.wizard.needs_api", "puffpai.integrate.api",
                                     "puffpai.webapps.api", "puffpai.box.api"])
def test_the_blueprints_are_registered_on_the_backend(modname):
    from puffpai import api_server
    blueprint = importlib.import_module(modname).bp
    assert blueprint.name in api_server.app.blueprints


@merged_only
def test_the_terminal_has_the_round3_commands():
    import argparse
    from puffpai import cli
    added = cli.add_round3_parsers(argparse.ArgumentParser().add_subparsers())
    assert added == list(cli.ROUND3_PARSER_MODULES)
