"""The public docs say what this code does, and cannot drift from it.

* The README's "What computer you need" box: every memory tier is read back
  out of the text and checked against ``config/model_catalog.json`` and
  ``config/model_profiles.json`` (which models fit, what they can do, where
  they run comfortably, how big the download is).
* The "Minimum requirements" table matches the catalog row for row, and the
  quoted below-the-floor sentence is the one the wizard prints.
* No em dash (and no spaced en dash used as one) in README.md or docs/*.md.
* Zone 3 in the README describes the "What leaves your computer" page, not a
  retired posture name or a fixed number of doors, and nothing owner-facing
  claims BuildBot ties into or extends into firewalls or routers. It only asks
  what gear you run and suggests a setting; it never touches that gear.
* Every `puffpai <command>` the docs name (and the argument after it,
  in code) is one ``cli.build_parser()`` has; nothing describes the retired
  14-step wizard; the summary's parts are quoted exactly as the wizard prints
  them; and every picture the docs show is in the repo.

Run:
    pytest tests/test_docs_truth.py
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _catalog() -> list[dict]:
    return json.loads(_read("config/model_catalog.json"))["models"]


def _tier(model_id: str) -> str:
    profiles = json.loads(_read("config/model_profiles.json"))["profiles"]
    return profiles[model_id]["routing_tier"]


def _box() -> str:
    text = _read("README.md")
    start = text.index("**What computer you need**")
    return text[start:text.index("## Install", start)]


def _memory_tiers() -> list[tuple[float, float, str]]:
    """(low, high, text) per memory bullet; high is inf for "or more", low is 0
    for "Under"."""
    box = _box()
    bullets = re.split(r"\n>\s{3}- ", box)[1:]
    tiers = []
    for raw in bullets:
        text = " ".join(line.lstrip("> ").strip()
                        for line in raw.split("\n> - ")[0].splitlines())
        if m := re.match(r"\*\*(\d+(?:\.\d+)?) GB or more:\*\*", text):
            tiers.append((float(m[1]), float("inf"), text))
        elif m := re.match(r"\*\*(\d+(?:\.\d+)?) to (\d+(?:\.\d+)?) GB:\*\*",
                           text):
            tiers.append((float(m[1]), float(m[2]), text))
        elif m := re.match(r"\*\*Under (\d+(?:\.\d+)?) GB:\*\*", text):
            tiers.append((0.0, float(m[1]), text))
        else:
            pytest.fail(f"memory bullet this test cannot read: {text!r}")
    return sorted(tiers)


#: What each routing tier's line must say, in the words the box uses.
TIER_WORDS = {
    "light": "your own words",
    "guided": "one thing per message",
    "rails": "set phrases",
}


def _size(model_id: str) -> str:
    """'7B' for qwen2.5:7b-instruct, '1.5B' for qwen2.5:1.5b-instruct."""
    tag = model_id.split(":", 1)[1]
    return re.match(r"(\d+(?:\.\d+)?)b", tag)[1] + "B"


# -- the box ------------------------------------------------------------------

def test_the_box_tiers_cover_all_memory_without_gaps():
    tiers = _memory_tiers()
    floors = [float(m["min_ram_gb"]) for m in _catalog()]
    assert tiers[0][0] == 0.0 and tiers[0][1] == min(floors), (
        "the lowest bullet must be 'Under <smallest min_ram_gb>'")
    assert tiers[-1][1] == float("inf") and tiers[-1][0] == max(floors), (
        "the top bullet must start at the largest min_ram_gb")
    for (_, high, _), (low, _, _) in zip(tiers, tiers[1:]):
        assert high == low, f"the box skips or overlaps memory at {high}/{low} GB"


def test_under_the_floor_the_box_says_nothing_fits():
    low, high, text = _memory_tiers()[0]
    assert not [m for m in _catalog() if float(m["min_ram_gb"]) < high]
    assert "no model fits" in text


@pytest.mark.parametrize("index", range(1, 5))
def test_each_box_tier_is_true_of_the_catalog(index):
    tiers = _memory_tiers()
    assert len(tiers) == 5, "one bullet per tier plus 'Under'"
    low, high, text = tiers[index]
    catalog = _catalog()
    newly_fit = [m for m in catalog if low <= float(m["min_ram_gb"]) < high]
    assert newly_fit, f"no model starts fitting between {low} and {high} GB"

    routing = {_tier(m["id"]) for m in newly_fit}
    assert len(routing) == 1, f"{low}-{high} GB mixes tiers {routing}"
    tier = routing.pop()
    assert TIER_WORDS[tier] in text, (
        f"{low}-{high} GB is the {tier} tier; its line must say "
        f"{TIER_WORDS[tier]!r}: {text!r}")
    for other, words in TIER_WORDS.items():
        if other != tier:
            assert words not in text, f"{low}-{high} GB claims {other}: {text!r}"

    biggest = max(float(m["min_ram_gb"]) for m in catalog)
    for model in newly_fit:
        if float(model["min_ram_gb"]) == biggest:
            assert "biggest model" in text
        else:
            assert re.search(rf"\b{re.escape(_size(model['id']))}\b", text), (
                f"{model['id']} fits from {low} GB; the line must name its size")

    comfy = re.search(r"(\d+) GB runs it comfortably", text)
    if comfy:
        best = max(newly_fit, key=lambda m: float(m["min_ram_gb"]))
        assert float(comfy[1]) == float(best["rec_ram_gb"]), (
            f"{best['id']} is comfortable from {best['rec_ram_gb']} GB")


def test_the_box_download_range_matches_the_catalog():
    disks = [m["disk_gb"] for m in _catalog()]
    m = re.search(r"plus (\d+) to (\d+) GB for the AI model",
                  " ".join(_box().replace(">", " ").split()))
    assert m, "the box must give the model download range"
    assert (int(m[1]), int(m[2])) == (min(disks), max(disks))


# -- Minimum requirements -----------------------------------------------------

def test_the_minimum_requirements_table_is_the_catalog():
    text = _read("README.md")
    section = text[text.index("### Minimum requirements"):]
    rows = set()
    for m in re.finditer(r"^\| (\d+) GB \| (.+?) \| (\d+) GB \|$", section,
                         re.M):
        for model_id in re.findall(r"`([^`]+)`", m[2]):
            rows.add((model_id, float(m[1]), float(m[3])))
    catalog = {(m["id"], float(m["min_ram_gb"]), float(m["disk_gb"]))
               for m in _catalog()}
    assert rows == catalog


def test_the_quoted_floor_sentence_is_the_one_the_wizard_prints():
    from puffpai import model_catalog
    message = model_catalog.fit_menu({"memory": {"total_gb": 1.0}})["message"]
    text = _read("README.md")
    # The first quotation in the section is the wizard's own sentence.
    quote = re.search(r'\n\n((?:> .*\n)+)',
                      text[text.index("### Minimum requirements"):])
    assert quote, "the README must quote what the wizard says below the floor"
    quoted = " ".join(line[2:].strip() for line in quote[1].splitlines())
    assert quoted.strip('"') == message
    assert "refuses" not in text[text.index("### Minimum requirements"):
                                 text.index("### Minimum requirements") + 1500]


# -- dashes ---------------------------------------------------------------------

def _public_docs() -> list[Path]:
    return [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md"))]


@pytest.mark.parametrize("path", _public_docs(), ids=lambda p: p.name)
def test_no_em_dashes_in_public_docs(path):
    bad = [f"{path.name}:{n}: {line.strip()}"
           for n, line in enumerate(path.read_text(encoding="utf-8")
                                    .splitlines(), 1)
           if "—" in line or " – " in line]
    assert not bad, "em dash in public docs:\n" + "\n".join(bad)


# -- Zone 3 and the security gear ------------------------------------------------

def _zone3() -> str:
    text = _read("README.md")
    start = text.index("- **Zone 3, the Bridge.**")
    return text[start:text.index("\n---", start)]


def test_readme_zone3_describes_the_outside_page_not_a_door_count():
    zone3 = _zone3()
    assert "What leaves" in zone3 and "your computer" in zone3
    assert "depends on the build" in zone3
    assert "moat" not in _read("README.md").lower()
    numbers = r"\d+|one|two|three|four|five|six|seven|eight|nine|ten"
    assert not re.search(rf"\b({numbers})\s+(outbound\s+|outside\s+)?doors?\b",
                         zone3, re.I)


def test_readme_says_the_bridge_never_touches_your_gear():
    zone3 = " ".join(_zone3().split())
    assert ("works alongside the security gear you already run, and never "
            "touches it") in zone3


#: Claims that BuildBot controls or integrates with a firewall or router. It
#: does not: zone_bridge only asks what gear you run and suggests a setting.
GEAR_CLAIMS = re.compile(
    r"\b(ties into|tie into|tied into|tying into|extends? into|"
    r"extends? its reach)\b", re.I)


def _owner_facing() -> list[Path]:
    paths = _public_docs()
    paths += sorted((ROOT / "src/puffpai/wizard").rglob("*.py"))
    paths += sorted((ROOT / "src/puffpai/wizard").rglob("*.json"))
    paths += [ROOT / "src/puffpai" / name for name in (
        "zone_bridge.py", "self_knowledge.py", "outside_services.py",
        "outside_cli.py", "outside_api.py")]
    paths += [ROOT / "config/bridge_postures.json"]
    paths += sorted((ROOT / "dashboard/app/dashboard").rglob("*.tsx"))
    return [p for p in paths if p.is_file()]


def test_nothing_owner_facing_claims_to_tie_into_security_gear():
    found = []
    for path in _owner_facing():
        for n, line in enumerate(path.read_text(encoding="utf-8",
                                                errors="replace")
                                 .splitlines(), 1):
            if GEAR_CLAIMS.search(line):
                found.append(f"{path.relative_to(ROOT)}:{n}: {line.strip()}")
    assert not found, ("BuildBot never touches your firewall or router; say "
                       "it works alongside them:\n" + "\n".join(found))


def test_the_gear_scan_covers_what_it_says():
    names = {p.name for p in _owner_facing()}
    assert {"README.md", "GETTING_STARTED.md", "zone_bridge.py",
            "self_knowledge.py", "bridge_postures.json"} <= names
    assert any(p.suffix == ".tsx" for p in _owner_facing())


# -- the docs describe this product, not the retired one ----------------

def _doc_texts() -> list[tuple[str, str]]:
    return [(p.relative_to(ROOT).as_posix(), p.read_text(encoding="utf-8"))
            for p in _public_docs()]


#: `puffpai <command>`, also as ./puffpai, puffpai.cmd or puffpai.exe. Not a
#: path (src/puffpai/...), not a variable (PUFFPAI_...), not a module import.
_COMMAND = re.compile(
    r"(?<![\w.\-/])(?:\./)?puffpai(?:\.cmd|\.exe)?[ \t]+([a-z][\w:-]*)"
    r"(?:[ \t]+([a-z][\w:-]*))?")
_CODE = re.compile(r"```.*?```|`[^`\n]+`", re.S)


def _named(text: str) -> list[tuple[str, str | None]]:
    """(command, next word) for each `puffpai <command>` in *text*."""
    return [m.groups() for m in _COMMAND.finditer(text) if m[1] != "import"]


def _commands() -> dict:
    """{command: set of words its first argument may be, or None for any}."""
    import argparse
    from puffpai import cli
    parser = cli.build_parser()
    table: dict = {}
    for action in parser._actions:
        if not isinstance(action, argparse._SubParsersAction):
            continue
        for name, sub in action.choices.items():
            allowed = None
            for inner in sub._actions:
                if isinstance(inner, argparse._SubParsersAction):
                    allowed = set(inner.choices)
                    break
                if not inner.option_strings and inner.dest != "help":
                    allowed = set(inner.choices) if inner.choices else None
                    break
            table[name] = allowed
    return table


def test_the_command_scan_reads_what_it_should():
    found = _named(
        "run `puffpai sources search dinosaurs`, ./puffpai where, "
        "from puffpai import cli, src/puffpai/cli.py, PUFFPAI_STATE_DIR")
    assert found == [("sources", "search"), ("where", None)]
    assert _commands()["outside"] >= {"list", "on", "off", "log"}


@pytest.mark.parametrize("path", _public_docs(), ids=lambda p: p.name)
def test_every_puffpai_command_the_docs_name_exists(path):
    rel, text = path.name, path.read_text(encoding="utf-8")
    commands = _commands()
    bad = []
    for command, _ in _named(text):
        if command not in commands:
            bad.append(f"puffpai {command}")
    # The word after a command is checked only inside code, where it is
    # the command's argument and not the next word of a sentence.
    for block in _CODE.finditer(text):
        for command, word in _named(block.group(0)):
            allowed = commands.get(command)
            if word and allowed is not None and word not in allowed:
                bad.append(f"puffpai {command} {word}")
    assert not bad, f"{rel} names commands `puffpai` does not have: {bad}"


#: What the old product said and this one does not have.
RETIRED = ("of 14", "14 steps", "SETUP_STEPS", "moat", "allowlist",
           "crew sizing")


@pytest.mark.parametrize("path", _public_docs(), ids=lambda p: p.name)
def test_the_docs_do_not_describe_the_retired_wizard(path):
    rel, text = path.name, path.read_text(encoding="utf-8")
    low = text.lower()
    found = [word for word in RETIRED if word.lower() in low]
    assert not found, f"{rel} still says {found}"


#: The sections "Apps and code", "Pictures" and "The locked box" are
#: drawn only when the needs step found something for them; the docs list the
#: eight every plan has.
_ROUND3_SECTIONS = ("apps_code", "pictures", "box")


def _summary_headings() -> list[str]:
    from puffpai.wizard import proposal, wording
    return [wording.say("section_" + section) for section in proposal.SECTIONS
            if section not in _ROUND3_SECTIONS]


def test_the_summary_headings_are_the_eight_the_wizard_prints():
    headings = _summary_headings()
    assert headings[0] == "How it thinks" and len(headings) == 8


#: The docs list the summary's parts as "- **<heading>.** ..." bullets under
#: a line that says "Its parts, in order:".
_PARTS = re.compile(r"Its parts, in order:\s*\n\n((?:- \*\*.+\n(?:  .+\n)*)+)")


def test_the_docs_quote_the_summary_headings_exactly_and_in_order():
    headings = _summary_headings()
    seen = 0
    for rel, text in _doc_texts():
        for block in _PARTS.finditer(text):
            quoted = re.findall(r"^- \*\*(.+?)\.\*\*", block[1], re.M)
            assert quoted == headings, (
                f"{rel} lists the summary as {quoted}; the wizard prints "
                f"{headings}")
            seen += 1
    assert seen >= 2, "README and GETTING_STARTED both list the summary"


def test_the_example_transcript_headings_are_real():
    """In the example run, a line after a blank line with an indented line
    under it is a summary heading, and it must be one the wizard prints."""
    headings = set(_summary_headings())
    text = _read("docs/GETTING_STARTED.md")
    start = text.index("Here is the plan for")
    block = text[start:text.index("Total to download", start)].splitlines()
    found = [line for before, line, after in zip(block, block[1:], block[2:])
             if not before.strip() and line.strip() and not line[0].isspace()
             and after.startswith("  ") and not line[0].isdigit()]
    assert found and set(found) <= headings, sorted(set(found) - headings)


def test_every_image_the_docs_show_exists():
    missing = []
    for rel, text in _doc_texts():
        base = (ROOT / rel).parent
        for target in re.findall(r"!\[[^\]]*\]\(([^)\s]+)\)", text):
            if "://" in target:
                continue
            if not (base / target).is_file():
                missing.append(f"{rel}: {target}")
    assert not missing, missing
