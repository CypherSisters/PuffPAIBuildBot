"""Two promises kept honest by grep.

1. The "ask me before every outside visit" style is gone. Nothing ever enforced it; no shipped text may claim it, the
   wizard may not offer it, and an old record that still says "moat" is read
   as "fenced" with one plain note.
2. Zero telemetry: the web page's build tool (Next.js) reports usage to its
   maker unless NEXT_TELEMETRY_DISABLED=1 is set. Both installers, every
   launcher they write, and the dashboard's env template set it. And the
   installers make a secret file owner-only BEFORE the secret goes in.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from puffpai import zone_bridge

ROOT = Path(__file__).resolve().parents[1]

#: Claims that the bot asks before each outside visit. Negations ("was never
#: built") are fine; a claim or an offer is not.
_ASK_FIRST_CLAIMS = re.compile(
    r"asks? (?:you|me) first"
    r"|ask me (?:before each|before every|every time)"
    r"|asks? (?:you |me )?before (?:each|every) outside visit"
    r"|every outbound call asks"
    r"|stricter \(asking",
    re.I)

_SHIPPED = [ROOT / "src", ROOT / "config", ROOT / "docs",
            ROOT / "dashboard" / "app"]
_EXTS = {".py", ".json", ".md", ".ts", ".tsx", ".txt"}


def _shipped_files():
    for base in _SHIPPED:
        for path in base.rglob("*"):
            if path.suffix in _EXTS and "node_modules" not in path.parts \
                    and "catalog_snapshots" not in path.parts:
                yield path
    for name in ("README.md", "SECURITY.md"):
        if (ROOT / name).exists():
            yield ROOT / name


def test_no_shipped_text_claims_it_asks_before_every_outside_visit():
    hits = []
    for path in _shipped_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        for m in _ASK_FIRST_CLAIMS.finditer(text):
            hits.append(f"{path.relative_to(ROOT)}: {m.group(0)!r}")
    assert not hits, "\n".join(hits)


def test_the_ban_itself_bites():
    for claim in ("Your bot asks you first.", "Ask me before each outside visit",
                  "Stricter (asking every time is not built yet)",
                  "every outbound call asks you"):
        assert _ASK_FIRST_CLAIMS.search(claim), claim


def test_the_wizard_no_longer_offers_the_per_visit_choice():
    ids = [p["id"] for p in zone_bridge.posture_menu()]
    assert ids == ["open_yard", "fenced", "drawbridge_up"]
    assert zone_bridge.posture_for_environment(False, False, False)[
        "recommended"] == "fenced"


def test_an_old_moat_record_reads_as_fenced_with_one_plain_note():
    old = {"posture": "moat", "label": "Stricter (asking every time is not "
           "built yet)", "strictness": 2, "chosen_by": "owner"}
    new = zone_bridge.migrate_record(old)
    assert new["posture"] == "fenced" and new["migrated_from"] == "moat"
    assert new["note"] == zone_bridge.MOAT_FOLDED_NOTE
    assert "each outside service asks you once" in new["note"]
    assert new["chosen_by"] == "owner"
    assert old["posture"] == "moat", "the input is not changed in place"
    assert zone_bridge.migrate_record({"posture": "fenced"}) == {"posture": "fenced"}


def test_a_moat_request_is_recorded_as_fenced_never_looser():
    posture, note = zone_bridge.reconcile_with_egress("moat", "island")
    assert posture == "fenced" and zone_bridge.MOAT_FOLDED_NOTE in note
    assert zone_bridge.reconcile_with_egress("moat", "sealed")[0] == "drawbridge_up"
    block = zone_bridge.bridge_manifest_block("moat")
    assert block["posture"] == "fenced" and block["note"]


def test_build_self_shows_an_old_record_migrated():
    from puffpai import api_server
    out = api_server._build_self_bridge({"bridge": {"posture": "moat",
                                                    "label": "Stricter"}})
    assert out["posture"] == "fenced"
    assert out["note"] == zone_bridge.MOAT_FOLDED_NOTE


# ── zero telemetry ───────────────────────────────────────────────────────────

def _read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


@pytest.mark.parametrize("rel", ["install.ps1", "install.sh"])
def test_installers_switch_next_telemetry_off_everywhere(rel):
    text = _read(rel)
    # For the installer's own build, the env file every launcher loads, the
    # dashboard's .env.local, the run-dashboard script and the start helper.
    assert text.count("NEXT_TELEMETRY_DISABLED") >= 5, rel
    first = text.index("NEXT_TELEMETRY_DISABLED")
    assert first < text.index("npm"), "set before anything is built"


def test_the_dashboard_env_template_switches_it_off():
    assert re.search(r"^NEXT_TELEMETRY_DISABLED=1$", _read("dashboard/.env.example"),
                     re.M)


def test_ps1_makes_secret_files_owner_only_before_writing_them():
    text = _read("install.ps1")
    body = text[text.index("function Write-Secret"):]
    body = body[:body.index("\n}\n") if "\n}\n" in body else body.index("\r\n}\r\n")]
    assert body.index("Protect-File $path") < body.index("Set-Content")
    assert "New-Item -ItemType File" in body
    protect = text[text.index("function Protect-File"):]
    protect = protect[:protect.index("function Get-EnvFileValue")]
    assert "$LASTEXITCODE" in protect and "Fail" in protect
    assert "AreAccessRulesProtected" in protect


def test_sh_makes_secret_files_owner_only_before_writing_them():
    text = _read("install.sh")
    body = text[text.index("write_secret() {"):]
    body = body[:body.index("\n}\n")]
    assert body.index("chmod 600") < body.index("printf '%s\\n' \"$_body\" >")
    assert "fail" in body and "stat" in body
