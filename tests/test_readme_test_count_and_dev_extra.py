"""A claim a reader can check in ten seconds must not be a number that rots.

The README said "1320 tests are
collected in this tree (`py -m pytest --co -q`)". The real count on the
cold clone was 1333 - and the quoted command *fails* right after the
documented install, because pytest is in the `[dev]` extra and the Quickstart
installs the base package.

A number that goes stale on the next commit is the wrong shape for the claim.
This file pins the shape, not the number.

Run:
    pytest tests/test_readme_test_count_and_dev_extra.py
"""
from __future__ import annotations

import os
import re

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(relpath: str) -> str:
    with open(os.path.join(_REPO_ROOT, relpath), "r", encoding="utf-8") as fh:
        return fh.read()


def test_the_readme_states_no_literal_test_count():
    readme = _read("README.md")
    assert "1320 tests" not in readme
    stale = re.findall(r"(\d[\d,]{2,})\s+tests are collected", readme)
    assert stale == [], (
        f"README quotes a literal test count ({stale}). It will be wrong by "
        "the next commit; describe how to run the suite instead.")


def test_the_readme_says_how_to_run_the_suite_including_the_dev_extra():
    readme = _read("README.md")
    assert 'pip install -e ".[dev]" && py -m pytest' in readme
    assert "`[dev]` extra" in readme


def test_the_quickstart_mentions_the_dev_extra_where_the_install_happens():
    readme = _read("README.md")
    quickstart = readme.split("## Quickstart", 1)[1]
    install_block = quickstart.split("puffpai setup", 1)[0]
    assert "[dev]" in install_block, (
        "README's Quickstart installs the base package with no hint that the "
        "tests need the [dev] extra.")


def test_getting_started_says_the_dev_extra_is_what_makes_pytest_work():
    doc = _read(os.path.join("docs", "GETTING_STARTED.md"))
    row = [line for line in doc.splitlines() if '".[dev]"' in line]
    assert row, "GETTING_STARTED no longer lists the [dev] extra."
    assert any("py -m pytest" in line for line in row), (
        "The [dev] row does not say that it is what `py -m pytest` needs.")


def test_pytest_really_is_only_in_the_dev_extra():
    # The reason the doc has to say it. If pytest ever moves into the base
    # install, this test fails and the wording above becomes wrong.
    setup_py = _read("setup.py")
    dev_block = setup_py.split('"dev"', 1)
    assert len(dev_block) == 2, "setup.py no longer declares a 'dev' extra."
    assert "pytest" in dev_block[1][:400]
