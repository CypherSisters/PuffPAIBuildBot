"""The Developer pages and the small fixes that ride with them (source checks).

The pages are proved to render by the headless run; these
tests pin what the source must keep saying:

* the sidebar has "Developer" between Looks and Settings, always shown;
* every Developer page has the Use CLI panel, whose words come from the
  backend list, and says "Open PowerShell ... then paste" (backend wording);
* the proxy may reach /developer, and the Developer pages call nothing new
  that could change something except the routes setup already uses and the
  command line's shell session;
* no Developer page puts a secret on screen or in the browser's storage;
* the banned list holds on every Developer page;
* the Device page reads "connected" (what /build/self really says);
* the YOUR PRIVATE STUFF card reads the live vault state.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import glob
import os
import re

import pytest

from test_plain_words_in_ui_strings import banned_in, tsx_strings

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(ROOT, "dashboard", "app")
DEV = os.path.join(APP, "dashboard", "developer")
DEV_FILES = sorted(glob.glob(os.path.join(DEV, "**", "*.ts*"), recursive=True))
ZONE_PAGES = [os.path.join(DEV, p, "page.tsx") for p in ("vault", "brain", "bridge")]


def _read(*parts: str) -> str:
    with open(os.path.join(APP, *parts), encoding="utf-8") as fh:
        return fh.read()


def test_the_sidebar_has_developer_between_looks_and_settings():
    src = _read("dashboard", "_lib", "menuAside.ts")
    hrefs = re.findall(r'href: "([^"]+)"', src)
    i = hrefs.index("/dashboard/developer")
    assert hrefs[i - 1] == "/dashboard/looks" and hrefs[i + 1] == "/dashboard/settings"
    block = src[src.index('href: "/dashboard/developer"'):]
    block = block[:block.index("}")]
    assert 'label: "Developer"' in block and 'railGroup: "settings"' in block


def test_no_surface_mapping_hides_developer():
    assert "/dashboard/developer" not in _read("_lib", "surfaceMap.ts")


def test_the_proxy_may_reach_the_developer_routes():
    src = _read("_lib", "server", "proxyAllowlist.ts")
    assert re.search(r'^\s*"developer",', src, re.M)


@pytest.mark.parametrize("path", ZONE_PAGES)
def test_every_zone_page_has_the_use_cli_panel(path):
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    page = os.path.basename(os.path.dirname(path))
    assert f'<UseCliPanel page="{page}" />' in src


def test_the_panel_shows_only_the_backends_words():
    src = _read("dashboard", "developer", "_components", "UseCliPanel.tsx")
    # The commands, the "open a terminal" line and the folder step all come
    # from the backend's list; the panel writes no command of its own.
    for field in ("panel.open_terminal[shell]", "panel.go_to_folder[shell]",
                  "c[shell]", "panel.no_command_yet", "panel.secrets_line"):
        assert field in src, field
    assert "puffpai " not in src


def test_the_developer_pages_change_things_only_through_existing_routes():
    calls = set()
    for path in DEV_FILES:
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        found = set(re.findall(r"\b(save\w+|test\w+|apiPost|fetch)\(", src))
        if path.endswith("developerService.ts"):
            # The command line: a shell session on /developer/terminal, bound
            # to this paired device (tests/test_developer_terminal.py).
            assert src.count("fetch(") == 1 and "/api/pai${path}" in src
            assert set(re.findall(r'send<[^>]+>\("([^"]+)"', src)) == {
                f"/developer/terminal/{r}"
                for r in ("open", "input", "output", "interrupt", "close")}
            found.discard("fetch")
        if path.endswith("addYourOwnService.ts"):
            # "Add your own": only the programs and files routes (their
            # backend asks its own questions), one fetch through the proxy.
            assert src.count("fetch(") == 1 and "/api/pai${path}" in src
            assert set(re.findall(r'call(?:<[^>]+>)?\(\s*[`"](/[a-z]+)', src)) == {
                "/programs", "/files"}
            found.discard("fetch")
        calls |= found
    # Brain: the model pick setup already has. Home Assistant and her own
    # tools are the shared ToolServers panels (the /mcp routes, checked below).
    assert calls <= {"saveOllama"}, calls


@pytest.mark.parametrize("path", DEV_FILES)
def test_no_developer_page_keeps_or_shows_a_secret(path):
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    for bad in ("localStorage", "sessionStorage", "dangerouslySetInnerHTML",
                "recovery_code", "api_key", "X-Vault-Token", "--ha-token",
                "--vault-pin", "--password", "--value"):
        assert bad not in src, (path, bad)


def test_the_home_assistant_token_box_is_hidden_and_cleared():
    page = _read("dashboard", "developer", "brain", "page.tsx")
    assert '<ConnectHomeAssistant title="Home Assistant" />' in page
    assert "<AddOwnTool hideHomeAssistant />" in page
    src = _read("_components", "ToolServers", "ConnectHomeAssistant.tsx")
    box = src[src.index('aria-label="Home Assistant token"'):]
    assert 'type="password"' in box[:200]
    assert 'setToken("")' in src
    tool = _read("_components", "ToolServers", "AddOwnTool.tsx")
    box = tool[tool.index('aria-label="Tool password"'):]
    assert 'type="password"' in box[:200]
    assert 'setPassword("")' in tool


@pytest.mark.parametrize("name", ["ConnectHomeAssistant.tsx", "AddOwnTool.tsx"])
def test_the_tool_server_panels_keep_no_secret_and_use_only_the_mcp_routes(name):
    src = _read("_components", "ToolServers", name)
    for bad in ("localStorage", "sessionStorage", "dangerouslySetInnerHTML",
                "api_key", "--password"):
        assert bad not in src, (name, bad)
    service = _read("_lib", "services", "mcpService.ts")
    assert set(re.findall(r'req<\w+>\(\s*[`"](/[a-z-]+)', service)) == {"/mcp"}


def test_the_model_pick_never_changes_the_address():
    src = _read("dashboard", "developer", "brain", "page.tsx")
    assert re.findall(r"saveOllama\(([^)]*)\)", src) == ["id"]


@pytest.mark.parametrize("path", DEV_FILES + [
    os.path.join(APP, "_components", "CopyCommand.tsx"),
    # Rendered on the Bridge page, so its words are Developer-page words too.
    os.path.join(APP, "dashboard", "settings", "PairedDevicesCard.tsx")])
def test_the_round3_banned_words_hold_on_the_developer_pages(path):
    for where, text in tsx_strings(path):
        assert not banned_in(text), (where, text, banned_in(text))


def test_the_device_page_reads_connected():
    src = _read("dashboard", "device", "page.tsx")
    assert 'brain.backend_status === "connected"' in src
    assert 'backend_status === "up"' not in src


def test_the_private_stuff_card_reads_the_live_vault_state():
    src = _read("dashboard", "_components", "AsideMenu", "index.tsx")
    assert 'apiGet<PrivateState>("/system/privacy"' in src
    card = src[src.index("function PrivateStuffCard"):]
    card = card[:card.index("\n}\n")]
    assert "vault ===" in card and "outside_services_on" in src
    assert 'href="/dashboard/developer"' in card


def test_the_knowledge_page_uses_the_shared_copy_pill():
    src = _read("dashboard", "knowledge", "BuildSources.tsx")
    assert 'import CopyCommand from "../../_components/CopyCommand"' in src
    assert "function CopyCommand" not in src


# ── Bridge page: paired devices (acceptance A_wedding item 10) ──────────

CARD = ("dashboard", "settings", "PairedDevicesCard.tsx")
BRIDGE = ("dashboard", "developer", "bridge", "page.tsx")


def _jsx_block(src: str, start: str) -> str:
    """The text of one JSX element, from `start` to its closing `/>`."""
    i = src.index(start)
    return src[i:src.index("/>", i) + 2]


@pytest.mark.parametrize("path", DEV_FILES)
def test_no_developer_page_carries_the_pairing_code(path):
    # The code + QR come from PairDeviceCard via /api/pair/code; neither may be
    # reached from a Developer file. Adding a device is a link to /pair.
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    assert "PairDeviceCard" not in src, path
    assert "/api/pair/code" not in src, path
    assert "show-pairing-code" not in src, path


def test_the_bridge_page_uses_the_devices_card_without_the_code():
    src = _read(*BRIDGE)
    uses = re.findall(r"<PairedDevicesCard\b[^>]*/>", src)
    assert len(uses) == 1, uses
    assert "showAddDevice={false}" in uses[0]


def test_the_bridge_page_links_to_the_pairing_page_for_a_new_device():
    src = _read(*BRIDGE)
    link = src[src.index('<GoTo href="/pair"'):]
    link = link[:link.index("</GoTo>")]
    assert 'title="Add a device"' in link
    # A plain link: this page asks for no yes of its own and fetches nothing.
    assert "fetch(" not in src and "onClick" not in link


def test_the_devices_card_hides_the_code_only_when_asked():
    src = _read(*CARD)
    assert "showAddDevice = true" in src  # Settings keeps its code as before
    gated = src[src.index("{showAddDevice && ("):]
    gated = gated[:gated.index(")}")]
    assert "<PairDeviceCard />" in gated
    # The only place the code card is rendered is inside that gate.
    code = re.sub(r"/\*.*?\*/|\{/\*.*?\*/\}|//[^\n]*", "", src, flags=re.S)
    assert code.count("<PairDeviceCard />") == 1


def test_the_devices_card_reads_the_field_the_route_sends():
    route = _read("api", "pair", "devices", "route.ts")
    card = _read(*CARD)
    # The route answers { devices, self }; the card must mark "this device"
    # from `self`. It used to read `currentDeviceId`, which never came back,
    # so every fresh pair was called "an older pairing".
    assert re.search(r"\{\s*devices:\s*listPairedDevices\(\),\s*self:\s*pairedDeviceIdFor\(req\)\s*\}", route)
    assert "data?.self" in card
    assert "currentDeviceId" not in re.sub(r"/\*.*?\*/|//[^\n]*", "", card, flags=re.S)
    assert "setCurrentId(" in card and "isCurrent = currentId !== null && d.id === currentId" in card


def test_the_devices_card_never_blames_an_older_pairing():
    words = " ".join(t for _, t in tsx_strings(os.path.join(APP, *CARD)))
    assert "older pairing" not in words
    assert "next page load" not in words


def test_the_devices_card_names_the_bot():
    card = _read(*CARD)
    words = " ".join(t for _, t in tsx_strings(os.path.join(APP, *CARD)))
    assert "talk to PuffPAI" not in words
    assert "Every screen and phone that can talk to {talksTo}." in " ".join(card.split())
    assert 'botName && botName.trim() ? botName.trim() : "your bot"' in card
    bridge = _read(*BRIDGE)
    assert "botName={build?.build.name}" in _jsx_block(bridge, "<PairedDevicesCard")


def test_the_devices_card_has_no_em_dash_on_screen():
    for where, text in tsx_strings(os.path.join(APP, *CARD)):
        assert "—" not in text, (where, text)
