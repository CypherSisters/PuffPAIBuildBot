"""The whole wizard, run for real, and then asked what it actually wrote.

Every defect this module exists for survived a green test suite, and they
survived it the same way: the tests built their own fixtures. A hand-written
``{"persona": {"id": ...}}`` passed every check while no build in the world
wrote ``id``; a hand-made interview carried a goal that the real ``--yes`` run
never recorded. So nothing here is hand-made. The real ``runner.SCHEDULE`` is
walked with the real packages and the real runner entries, with a renderer that
answers the cards the way an owner would, and every assertion is made against
what came out: the manifest on disk, the folders on disk, the prompt the model
would be given, and ``prove``'s own verdict on all of it.

The build is a space bot called Infinity.

Nothing here reaches the network. Ollama is stubbed to answer nothing, the
source suggester is stubbed to suggest nothing, and no source is ticked for
download, so ``integrate`` runs with an empty fetch list.

Run:
    pytest tests/test_wizard_end_to_end.py
"""
from __future__ import annotations

import json
import os
import re
import types

import pytest

from puffpai import cli, setup_ops
from puffpai.config import get_config, get_state_dir
from puffpai.wizard import cards
from puffpai.wizard import runner as wizard_runner
from puffpai.wizard import sources_suggest

NAME = "Infinity"
GOAL = ("knows everything about space, space facts, current space events, "
        "astronomy, Earth weather events")
#: Long, not a digit run, and not a word in any refusal list.
PIN = "Telescope-Infinity-9042"

#: What a recovery code looks like when it is shown. Used only to type it back.
#: Groups of five (KDYKM-5YQ5K-...); the pairing code (Q9FH-YYX3) is two
#: groups of four inside a sentence and must not be taken for it.
_CODE_RE = re.compile(r"^[A-Z0-9]{5}(?:-[A-Z0-9]{5}){2,}$")


class Face:
    """A show-once face that is a person: it reads, and it types back.

    It keeps the last thing it was shown for exactly as long as it takes to
    answer the prompt, which is what the owner's eyes do. The test then asserts
    the value reached no file.
    """

    def __init__(self) -> None:
        self.blocks: list[list[str]] = []
        self._code = ""

    def show(self, lines) -> None:
        lines = [str(line) for line in (lines or ())]
        self.blocks.append(lines)
        for line in lines:
            token = line.strip()
            if _CODE_RE.match(token):
                self._code = token

    def ask(self, prompt: str) -> str:
        return self._code or "skip"


def _answers(card: dict, **_kw) -> dict:
    """What the owner types, by card. Everything else is "as you proposed"."""
    card_id = cards.card_id(card)
    if card_id == "purpose:goal":
        return {"goal": GOAL}
    if card_id == "purpose:identity":
        return {"name": NAME}
    if card_id == "purpose:place":
        # Yes to a place of its own, and the suggested id is accepted by not
        # typing one -- which is the path that gave a bot named Infinity the
        # id 'mybot' when the suggestion was worked out before the name was.
        return {"scaffold": True}
    if card_id == "secure:vault":
        return {"pin": PIN, "pin_again": PIN}
    if card_id == "brain":
        # No, do not download a model while I wait.
        return {"download_now": False} if "download_now" in {
            f["id"] for f in card["fields"]} else {}
    if card_id == "integrate":
        # Big downloads declined, explicitly, on the card that offers them.
        return {"fetch_now": []} if "fetch_now" in {
            f["id"] for f in card["fields"]} else {}
    return {}


@pytest.fixture
def built(tmp_path, monkeypatch):
    """One real wizard run in a temp state. Returns (run, face, cfg)."""
    monkeypatch.setenv("PUFFPAI_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(tmp_path / "data"))
    cfg = get_config(_force_reload=True)
    # No Ollama, no suggester, no network of any kind.
    monkeypatch.setattr(setup_ops, "list_models", lambda *a, **k: [])
    monkeypatch.setattr(setup_ops, "ollama_survey", lambda *a, **k: [])
    monkeypatch.setattr(sources_suggest, "search_terms",
                        lambda *a, **k: {"terms": [], "note": ""})

    args = types.SimpleNamespace(yes=False)
    face = Face()
    run = wizard_runner.Runner(
        args=args, cfg=cfg, non_interactive=False,
        runner_parts=cli._wizard_runner_parts(),
        renderer=_answers, show_once=face)
    assert run.refusals == []
    run.run()
    yield run, face, cfg
    get_config(_force_reload=True)


def _manifest() -> dict:
    from puffpai import build_manifest
    return build_manifest.read_manifest() or {}


def _check(record: dict, check_id: str) -> dict:
    for check in record["checks"]:
        if check["id"] == check_id:
            return check
    raise AssertionError(f"no check called {check_id}")


# -- what the run wrote --------------------------------------------------------

def test_the_run_finished_every_skill(built):
    run, _face, _cfg = built
    outcomes = {skill: row["outcome"] for skill, row in run.skill_status().items()}
    assert set(outcomes) == set(wizard_runner.SKILL_ORDER)
    assert [skill for skill, outcome in outcomes.items()
            if outcome not in ("done", "not_built")] == []


def test_the_persona_id_is_the_bots_own_word(built):
    """Not ``mybot``: the id is proposed after the name has been applied."""
    manifest = _manifest()
    persona = manifest["persona"]
    assert persona["app"] == "infinity"
    assert persona["pack"] == "infinity"
    assert persona["scaffolded"] is True
    assert persona["name"] == NAME
    assert persona["goal"] == GOAL
    assert "id" not in persona, "the block's real shape is app/pack"


def test_the_folders_are_on_disk_with_that_id(built):
    state = get_state_dir()
    app = os.path.join(state, "apps", "infinity", "app.json")
    pack = os.path.join(state, "packs", "infinity", "pack.json")
    assert os.path.isfile(app) and os.path.isfile(pack)
    with open(app, encoding="utf-8") as handle:
        assert json.load(handle)["id"] == "infinity"


def test_the_bots_own_page_is_on_the_allowlist(built):
    from puffpai import persona_scaffold

    surfaces = _manifest().get("surfaces") or {}
    allowlist = list(surfaces.get("allowlist") or [])
    surface = persona_scaffold.persona_surface("infinity")
    assert allowlist, "a trimmed build with no allowlist is not this build"
    assert surface in allowlist, (
        "the bot is invisible in its own navigation")


def test_the_goal_reaches_the_model(built):
    from puffpai import build_context

    goal = build_context.collect_goal()
    assert goal, "the model would be told the owner wrote nothing down"
    assert "space" in goal
    prompt = build_context.system_prompt(bot_name=NAME, goal=goal)
    assert GOAL[:40] in prompt


def test_prove_checks_the_persona_instead_of_shrugging(built):
    """The same class, checked on a build this test really made."""
    from puffpai import prove

    record = prove.static_prove()
    assert record["persona_id"] == "infinity"
    for check_id in ("id_sanity", "persona_visible", "goal_reaches_model"):
        check = _check(record, check_id)
        assert check["result"] != prove.NOT_APPLICABLE, check
        assert check["result"] != prove.FAIL, check
    assert not any("made no persona" in c["reason"] for c in record["checks"])


def test_the_manifest_and_the_run_record_are_both_on_disk(built):
    _run, _face, cfg = built
    assert os.path.isfile(os.path.join(cfg.CONFIG_DIR, "build_manifest.json"))
    assert os.path.isfile(os.path.join(cfg.CONFIG_DIR, "wizard_run.json"))


# -- what the run did NOT write ------------------------------------------------

def _every_file(root: str):
    for folder, _dirs, files in os.walk(root):
        for name in files:
            yield os.path.join(folder, name)


def test_no_secret_is_anywhere_under_the_state_dir(built, tmp_path):
    """The PIN and the recovery code exist in this process and nowhere else."""
    run, face, cfg = built
    shown = [line for block in face.blocks for line in block]
    code = next((line.strip() for line in shown
                 if _CODE_RE.match(line.strip())), "")
    assert code, "the vault ceremony showed no recovery code"

    for path in _every_file(str(tmp_path)):
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as handle:
                body = handle.read()
        except OSError:  # pragma: no cover - a locked file is not a leak
            continue
        assert PIN not in body, f"the vault PIN is in {path}"
        assert code not in body, f"the recovery code is in {path}"

    record = json.dumps(run.state())
    assert PIN not in record and code not in record
    # The log says a PIN was entered, which is a fact worth keeping, and says
    # nothing about what it was.
    vault_row = next(row for row in run.log if row["entry"] == "secure.vault")
    assert vault_row["changed"] == {"pin": "(entered, not recorded)",
                                    "pin_again": "(entered, not recorded)"}


def test_the_vault_is_sealed_and_the_record_says_only_that(built):
    vault = (_manifest().get("vault") or {})
    assert vault.get("configured") is True
    for banned in ("pin", "pin_hash", "recovery_code", "recovery_hash"):
        assert not vault.get(banned), banned
