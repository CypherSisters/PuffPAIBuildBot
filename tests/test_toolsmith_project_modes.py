"""Toolsmith sorts an unpacked project into one mode.

The trees are rebuilt from recorded evidence (``tests/fixtures/integrate/
classify_rows.json``, taken from the design team's GitHub corpus): the real
top-level file and folder names of each project. File bodies the evidence
did not record are small stand-ins (an empty lock file, a one-line module),
written by the same rules for every row; nothing is special-cased per project.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "fixtures", "integrate"))
import integrate_support as sup  # noqa: E402

from puffpai.integrate import classify  # noqa: E402

ROWS = sup.load_json("classify_rows.json")["rows"]
_DATA_DIRS = {"data", "dataset", "datasets"}
_CODE_EXT = {"Go": ".go", "TypeScript": ".ts", "JavaScript": ".js"}


def _materialise(root: str, row: dict) -> None:
    """Rebuild a tree from the recorded top-level names (generic rules)."""
    files = row["root_files"]
    for name in files:
        low = name.lower()
        if low == "package.json":
            scripts = {"build": "build it"} if row["package_json_build_script"] else {}
            sup.write(root, name, json.dumps({"name": "x", "scripts": scripts}))
        elif low.startswith("licen"):
            sup.write(root, name, sup.MIT_TEXT)
        elif low == "index.html":
            probe = row.get("index_html_probe") or {}
            if probe.get("bytes"):
                sup.write(root, name, sup.single_page_app(int(probe["bytes"]) - 200))
            else:
                # Not recorded: a page that loads the first source module next to it.
                module = next((f for f in files if f.endswith((".tsx", ".ts", ".jsx"))),
                              "src/main.js")
                sup.write(root, name, f'<div id="app"></div><script type="module" '
                                      f'src="./{module}"></script>')
        elif low.endswith((".md", ".txt")):
            sup.write(root, name, f"# {row['full_name'].split('/')[1]}\n")
        else:
            sup.write(root, name, "{}" if low.endswith((".json", ".lock", ".lockb", ".yaml"))
                      else "x\n")
    n_data = int(row.get("n_data_files") or 0)
    n_code = int(row.get("n_code_files") or 0)
    for d in row["root_dirs"]:
        if d.startswith("."):
            sup.write(root, f"{d}/config.yml", "x: 1\n")
        elif d in _DATA_DIRS:
            for i in range(n_data):
                sup.write(root, f"{d}/table_{i}.csv", "name,value\na,1\n")
            n_data = 0
        elif n_code:
            ext = ".py" if d == "examples" else ".go" if d in ("cmd", "internal") else ".ts"
            sup.write(root, f"{d}/main{ext}", "x = 1\n")
            n_code -= 1
        else:
            sup.write(root, f"{d}/notes.md", "notes\n")


@pytest.mark.parametrize("row", ROWS, ids=[r["full_name"] for r in ROWS])
def test_recorded_trees_sort_into_their_modes(tmp_path, row):
    root = str(tmp_path / "src")
    _materialise(root, row)
    mode, facts = classify.classify_tree(root)
    assert mode == row["expect"], (row["full_name"], facts)


def test_the_single_page_app_opens_as_it_is(tmp_path):
    row = next(r for r in ROWS if r["recorded_mode"] == "static-web-app-prebuilt")
    root = str(tmp_path / "src")
    _materialise(root, row)
    facts = classify.scan_tree(root)
    assert facts["entry_html"] == "index.html"
    assert not facts["entry"]["needs_build"]
    # The inline script counts as code (the scaffold check reads this).
    assert facts["code_bytes"] > 100_000


def test_a_page_that_loads_missing_or_typescript_files_needs_a_build(tmp_path):
    root = str(tmp_path)
    sup.write(root, "index.html", '<script type="module" src="/src/main.tsx"></script>')
    sup.write(root, "src/main.tsx", "export {}")
    facts = classify.scan_tree(root)
    assert facts["entry"]["needs_build"] is True
    assert classify.classify(facts) != "static_web"


def test_cdn_scripts_are_fine_for_a_static_page(tmp_path):
    root = str(tmp_path)
    sup.write(root, "docs/index.html",
              '<script src="https://cdn.example.org/lib.js"></script>'
              '<script src="app.js"></script>')
    sup.write(root, "docs/app.js", "console.log(1)")
    mode, facts = classify.classify_tree(root)
    assert mode == "static_web" and facts["entry_html"] == "docs/index.html"


def test_a_picture_folder_is_a_picture_set(tmp_path):
    root = str(tmp_path)
    for i in range(6):
        sup.write(root, f"img/p{i}.png", b"\x89PNG....")
    sup.write(root, "LICENSE", sup.MIT_TEXT)
    assert classify.classify_tree(root)[0] == "picture_set"


def test_a_command_package_is_a_cli_tool(tmp_path):
    root = str(tmp_path)
    sup.write(root, "package.json", json.dumps({"name": "t", "bin": {"t": "cli.js"}}))
    sup.write(root, "cli.js", "#!/usr/bin/env node\n")
    assert classify.classify_tree(root)[0] == "cli_tool"


def test_links_are_never_followed(tmp_path):
    outside = tmp_path / "outside"
    sup.write(str(outside), "secret.js", "x" * 50_000)
    root = tmp_path / "src"
    sup.write(str(root), "index.html", "<script>1</script>")
    try:
        os.symlink(str(outside), str(root / "link"), target_is_directory=True)
    except (OSError, NotImplementedError):
        try:  # Windows without the link right: a junction (a reparse point)
            import _winapi
            _winapi.CreateJunction(str(outside), str(root / "link"))
        except (ImportError, OSError, AttributeError):
            pytest.skip("this computer does not allow making links")
    facts = classify.scan_tree(str(root))
    assert facts["code_bytes"] < 1000
    assert not any("secret" in k for k in facts)
