"""The dashboard's own words pass the one plain-words check.

The shell (the top bar, the side rail and its card), the nav, the home page's
tiles and the pages added after the build are read as the owner sees them:
every piece of JSX text and every quoted string that reads as words. Code
(class names, CSS, ids, addresses) is left out. The same banned list as every
other owner-facing place (:mod:`puffpai.plain_words`), plus the few words a
dashboard shell grows by itself: an "Operator" chip, an "Arrange" button,
"telemetry" tiles and "nominal" status pills.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import os
import re

import pytest

from puffpai import plain_words as pw

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(os.path.dirname(HERE), "dashboard", "app")

#: The owner-visible shell, nav and tiles, and the pages added after a build.
FILES = (
    "dashboard/_components/NavBar/index.tsx",
    "dashboard/_components/NavBar/OperatorChip.tsx",
    "dashboard/_components/AsideMenu/index.tsx",
    "dashboard/_components/FooterBar.tsx",
    "dashboard/_lib/menuAside.ts",
    "dashboard/_lib/menuNavBar.ts",
    "dashboard/page.tsx",
    "dashboard/device/page.tsx",
    "dashboard/add/page.tsx",
    "dashboard/keys/page.tsx",
    "dashboard/looks/page.tsx",
    "_components/Front/FrontPage.tsx",
    "_components/Front/BotAppsGrid.tsx",
    "_components/Feed/FeedCardBody.tsx",
    "_components/Widgets/PersonaCard.tsx",
    "_components/Widgets/JetsonTile.tsx",
    "_components/CopyCommand.tsx",
    "dashboard/developer/page.tsx",
    "dashboard/developer/vault/page.tsx",
    "dashboard/developer/vault/ChangePlace.tsx",
    # Where the vault lives: the wizard's question and the
    # folder chooser both pages use.
    "onboarding/VaultWhere.tsx",
    "_components/VaultPlace/FolderPicker.tsx",
    "dashboard/developer/brain/page.tsx",
    "dashboard/developer/bridge/page.tsx",
    "dashboard/developer/developerService.ts",
    "dashboard/developer/_components/UseCliPanel.tsx",
    # The command line tile, its own window, and "Add a tool".
    "dashboard/developer/_components/CommandLine.tsx",
    "dashboard/developer/_components/CliTile.tsx",
    "dashboard/developer/terminal/page.tsx",
    "_components/AddATool.tsx",
    # One build foundation: the jobs home, a job's page, the
    # wizard's How lines, setup list and Yes, and the team's job lines.
    "_components/Jobs/BuildViews.tsx",
    "_components/Jobs/JobsHome.tsx",
    "_components/Jobs/jobParts.ts",
    "_lib/buildJobs.ts",
    "dashboard/jobs/[id]/page.tsx",
    "onboarding/BuildHandoff.tsx",
    # The walk around the pages (its sentences are checked in test_tour).
    "dashboard/_components/Walkthrough.tsx",
    "dashboard/developer/_components/parts.tsx",
    # The GitHub tile.
    "dashboard/developer/_components/GitHubTile.tsx",
    "dashboard/developer/overviewWords.ts",
    # Rendered on the Developer Bridge page, so its words are checked too.
    "dashboard/settings/PairedDevicesCard.tsx",
    # Tool servers: on the Brain and Smart Home pages and the wizard.
    "_components/ToolServers/ConnectHomeAssistant.tsx",
    "_components/ToolServers/AddOwnTool.tsx",
    # The home page tiles, every thing's own page and the Find more cards.
    "_components/Home/HomeTiles.tsx",
    "_components/Jobs/JobsHome.tsx",
    "dashboard/things/[id]/page.tsx",
    "dashboard/add/ThingsSection.tsx",
)

#: The ONE exception to the word check: the three
#: part names "Zone 1", "Zone 2" and "Zone 3" may be read inside the Developer
#: folder, and nowhere else. Only these exact spellings; any other "zone"
#: there ("zones", "security zone", "Zone 4") is still caught.
DEVELOPER_DIR = "dashboard/developer/"
DEVELOPER_ZONE_NAMES = re.compile(r"\bZone [123]\b")


def check_owner_text(rel: str, strings: list[str]) -> list[str]:
    """The plain-words check, with the Developer folder's one exception."""
    text = "\n".join(strings)
    if rel.replace("\\", "/").startswith(DEVELOPER_DIR):
        text = DEVELOPER_ZONE_NAMES.sub("", text)
    return pw.check(text, also=SHELL_WORDS)

#: Words a dashboard shell grows by itself; the rest is plain_words' list.
SHELL_WORDS = ("operator", "telemetry", "nominal", "scaffold", "backend offline",
               "arrange")

_COMMENTS = re.compile(r"/\*.*?\*/|(?<![:\"'])//[^\n]*", re.S)
_IMPORTS = re.compile(r"^\s*(?:import|export\s+\{)[^\n]*$", re.M)
_CLASSES = re.compile(r"className=(?:\"[^\"]*\"|\{`[^`]*`\}|\{[^{}]*\})")
_STRINGS = re.compile(r'"((?:[^"\\\n]|\\.)*)"' + r"|'((?:[^'\\\n]|\\.)*)'")
#: Text between tags. Code between a ">" and a "<" (a comparison, a call)
#: has brackets, operators or dotted names in it and is not taken.
_JSX_TEXT = re.compile(r">\s*([^<>{}=;()?!&|]*[A-Za-z][^<>{}=;()?!&|]*?)\s*<")
_CODE = re.compile(r"^[\w\-./:#%@()\[\]?&=+,*$|!~^ ]*$")


def _reads_as_words(text: str) -> bool:
    text = text.strip()
    if len(text) < 3 or not re.search(r"[A-Za-z]{3}", text):
        return False
    if " " not in text:
        # One word: only a capitalised word is owner text ("Arrange");
        # lowercase or joined tokens are ids, routes and CSS.
        return bool(re.fullmatch(r"[A-Z][a-z]+", text))
    if "var(--" in text or re.search(r"\d(?:px|rem|em|ms|s)\b", text):
        return False  # CSS
    words = text.split()
    if all(re.search(r"[-:/[\]]", w) or w.islower() and _CODE.match(w)
           for w in words) and not any(w[:1].isupper() for w in words):
        return False  # a list of class names or tokens
    return True


def owner_strings(source: str) -> list[str]:
    """The strings in a .tsx/.ts file an owner reads, one per item."""
    body = _IMPORTS.sub("", _COMMENTS.sub("", source))
    body = _CLASSES.sub("", body)
    out: list[str] = []
    for match in _STRINGS.finditer(body):
        text = match.group(1) if match.group(1) is not None else match.group(2)
        if _reads_as_words(text):
            out.append(text)
    for match in _JSX_TEXT.finditer(body):
        text = " ".join(match.group(1).split())
        if _reads_as_words(text) or re.fullmatch(r"[A-Z][a-z]+", text):
            out.append(text)
    return out


@pytest.mark.parametrize("rel", FILES)
def test_the_dashboard_shell_speaks_plain_words(rel):
    path = os.path.join(APP, *rel.split("/"))
    source = open(path, encoding="utf-8").read()
    strings = owner_strings(source)
    assert strings, f"no owner text found in {rel}: the reader is broken"
    bad = check_owner_text(rel, strings)
    assert not bad, (rel, bad)


def test_the_zone_names_pass_only_inside_the_developer_folder():
    names = ["Zone 1: Vault", "Zone 2: Brain", "Zone 3: Bridge"]
    assert not check_owner_text("dashboard/developer/page.tsx", names)
    assert not check_owner_text("dashboard/developer/vault/page.tsx", names)
    for rel in ("dashboard/vault/page.tsx", "dashboard/settings/page.tsx",
                "dashboard/_lib/menuAside.ts", "dashboard/developerx/page.tsx"):
        assert check_owner_text(rel, names), rel


@pytest.mark.parametrize("text", ["Three zones", "Your security zone",
                                  "Zone 4: Garden", "zone 1 is the vault",
                                  "Zone 12"])
def test_the_exception_is_narrow(text):
    assert check_owner_text("dashboard/developer/page.tsx", [text]), text


def test_no_page_outside_the_developer_folder_says_zone_1_2_or_3():
    found = []
    for folder, dirs, files in os.walk(APP):
        dirs[:] = [d for d in dirs if d not in ("node_modules", "__tests__")]
        rel_dir = os.path.relpath(folder, APP).replace(os.sep, "/")
        if (rel_dir + "/").startswith(DEVELOPER_DIR):
            continue
        for name in files:
            if not name.endswith((".ts", ".tsx")):
                continue
            with open(os.path.join(folder, name), encoding="utf-8") as fh:
                source = fh.read()
            for text in owner_strings(source):
                if DEVELOPER_ZONE_NAMES.search(text):
                    found.append((f"{rel_dir}/{name}", text))
    assert not found, found


def test_every_developer_file_is_read_by_the_word_check():
    dev = os.path.join(APP, "dashboard", "developer")
    on_disk = set()
    for folder, _dirs, files in os.walk(dev):
        for name in files:
            if name.endswith((".ts", ".tsx")):
                on_disk.add(os.path.relpath(os.path.join(folder, name), APP)
                            .replace(os.sep, "/"))
    assert on_disk and on_disk <= set(FILES), sorted(on_disk - set(FILES))


def test_the_developer_pages_name_their_zone():
    for rel, name in (("vault", "Zone 1: Vault"), ("brain", "Zone 2: Brain"),
                      ("bridge", "Zone 3: Bridge")):
        path = os.path.join(APP, "dashboard", "developer", rel, "page.tsx")
        with open(path, encoding="utf-8") as fh:
            assert name in owner_strings(fh.read()), rel


def test_the_reader_finds_what_the_owner_sees():
    got = owner_strings('''
        // a comment about the Operator
        const x = cond ? "Done" : "Arrange";
        <div className="flex-none zone-pill">SECURITY ZONE · AUTO</div>
        <span style={{ font: "700 11px var(--font)" }}>{label}</span>
        <Link href="/dashboard/keys">Sign-up keys</Link>
    ''')
    assert "Arrange" in got and "SECURITY ZONE · AUTO" in got
    assert "Sign-up keys" in got
    assert not any("Operator" in s or "zone-pill" in s or "/dashboard" in s
                   or "var(--" in s for s in got)
    assert pw.check("\n".join(got), also=SHELL_WORDS)
