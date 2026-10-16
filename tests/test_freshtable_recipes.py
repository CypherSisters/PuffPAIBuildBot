"""FreshTable recipes: the import can save them and the kitchen page keeps them.

Two rough edges found in review:

* ``ImportRecipes.tsx`` saves each ticked recipe through the
  ``fresh_create_recipe`` app action, but the action API's allowlist did not
  name it, so every save answered 403 and the modal still reported a count;
* the kitchen page (``public/freshtable-app.html``) pushed only
  ``{pantry, shoppingList, mealPlan}`` with PUT, and ``set_data`` saves
  exactly what it is given, so any edit there deleted every recipe that an
  upload or the import had written.

Run:
    pytest tests/test_freshtable_recipes.py
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile

_TMP_DATA_DIR = tempfile.mkdtemp(prefix="puffpai_bb_freshtable_test_")
os.environ.setdefault("PUFFPAI_DATA_DIR", _TMP_DATA_DIR)

import pytest  # noqa: E402

from puffpai import api_server  # noqa: E402
from puffpai.app_data import AppData  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KITCHEN_PAGE = os.path.join(REPO_ROOT, "dashboard", "public",
                            "freshtable-app.html")


@pytest.fixture
def store(tmp_path, monkeypatch):
    from puffpai import app_data as app_data_module
    monkeypatch.setattr(app_data_module, "_DATA_DIR", str(tmp_path))
    ad = AppData()
    monkeypatch.setattr(api_server, "_get_app_data", lambda: ad)
    return ad


def test_the_recipe_import_action_is_allowed(store):
    api_server.app.config.update(TESTING=True)
    client = api_server.app.test_client()
    auth = {"Authorization": f"Bearer {api_server._API_KEY}"}

    resp = client.post("/apps/freshtable/action", headers=auth, json={
        "action": "fresh_create_recipe",
        "params": {"name": "Pancakes", "ingredients": "flour, milk, eggs",
                   "instructions": "Mix and fry."},
    })
    assert resp.status_code == 200, resp.get_json()
    recipes = store.get_data("freshtable")["recipes"]
    assert [r["name"] for r in recipes] == ["Pancakes"]


def _sync_push_source() -> str:
    with open(KITCHEN_PAGE, encoding="utf-8") as fh:
        page = fh.read()
    match = re.search(r"function syncPush\(\) \{.*?\n\}\n", page, re.S)
    assert match, "syncPush() not found in freshtable-app.html"
    return match.group(0)


_HARNESS = r"""
const saved = JSON.parse(process.argv[process.argv.length - 1]);
const SYNC_API = '/api/pai/apps/freshtable';
let _syncTimer = null, _lastSyncTs = '';
let puts = [];
const state = {
  pantry: [{id: 'p1', name: 'Rice'}],
  shoppingList: [{id: 's1', name: 'Milk', checked: false}],
  mealPlan: [],
};
globalThis.setTimeout = (fn) => { fn(); return 0; };
globalThis.clearTimeout = () => {};
globalThis.fetch = async (url, opts) => {
  if (opts && opts.method === 'PUT') {
    puts.push(JSON.parse(opts.body));
    return {ok: true, json: async () => ({success: true})};
  }
  if (saved === null) return {ok: false, status: 503, json: async () => ({})};
  return {ok: true, json: async () => saved};
};
%SYNC_PUSH%
syncPush();
setImmediate(() => setImmediate(() => setImmediate(() =>
  console.log(JSON.stringify(puts)))));
"""


def _run_push(saved):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    script = _HARNESS.replace("%SYNC_PUSH%", _sync_push_source())
    out = subprocess.run([node, "-e", script, json.dumps(saved)],
                         capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_a_kitchen_page_edit_keeps_the_uploaded_recipes():
    recipe = {"id": "r1", "name": "Pancakes", "ingredients": "flour, milk",
              "instructions": "Mix and fry."}
    saved = {"pantry": [], "shoppingList": [], "mealPlan": [],
             "recipes": [recipe], "last_updated": "2026-10-15T09:00:00"}

    puts = _run_push(saved)

    assert len(puts) == 1
    assert puts[0]["recipes"] == [recipe]
    # The page still owns the three lists it edits.
    assert [i["name"] for i in puts[0]["pantry"]] == ["Rice"]
    assert [i["name"] for i in puts[0]["shoppingList"]] == ["Milk"]


def test_the_kitchen_page_does_not_push_over_a_document_it_could_not_read():
    assert _run_push(None) == []
