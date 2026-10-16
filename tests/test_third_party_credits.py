"""The credits file must name the single largest borrowed work.

The README points at
THIRD_PARTY_NOTICES.md for credit; that file had exactly two sections, both
package-manager manifests, and `grep -i "justboil\\|admin.one"` returned zero
hits - while the entire dashboard UI is derived from
justboil/admin-one-react-tailwind (MIT). Three dead template components
survived in the tree, one of them setting an `<Image src>` to a
`static.justboil.me` CDN URL. Unimported, so no runtime request; but nobody
reads an import graph before tweeting a screenshot.

MIT compliance was already satisfied by dashboard/LICENSE. This is the
credibility half.

Run:
    pytest tests/test_third_party_credits.py
"""
from __future__ import annotations

import os

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DASHBOARD_APP = os.path.join(_REPO_ROOT, "dashboard", "app")

DEAD_COMPONENTS = (
    os.path.join("dashboard", "app", "_components", "JustboilLogo"),
    os.path.join("dashboard", "app", "_components", "Section", "Banner",
                 "StarOnGitHub.tsx"),
    os.path.join("dashboard", "app", "_components", "StyleSelect",
                 "StylePickBox.tsx"),
)


def _read(relpath: str) -> str:
    with open(os.path.join(_REPO_ROOT, relpath), "r", encoding="utf-8") as fh:
        return fh.read()


# --- credit ---------------------------------------------------------------


def test_the_notices_file_credits_the_upstream_template():
    notices = _read("THIRD_PARTY_NOTICES.md")
    assert "## Upstream template" in notices
    assert "admin-one-react-tailwind" in notices
    assert "https://github.com/justboil/admin-one-react-tailwind" in notices
    assert "MIT" in notices


def test_the_readme_credits_section_names_it_too():
    # README's Credits section pointed at a file that omitted the biggest
    # borrow. Both ends now say it.
    readme = _read("README.md")
    assert "admin-one-react-tailwind" in readme
    assert "dashboard/LICENSE" in readme


def test_the_templates_own_license_still_ships_verbatim():
    # This is the part MIT actually requires; deleting it would trade a
    # credibility problem for a legal one.
    license_text = _read(os.path.join("dashboard", "LICENSE"))
    assert "JustBoil.me" in license_text
    assert "The MIT License" in license_text


# --- the dead components -------------------------------------------------


def test_the_dead_template_components_are_gone():
    for relpath in DEAD_COMPONENTS:
        assert not os.path.exists(os.path.join(_REPO_ROOT, relpath)), (
            f"{relpath} is back. It was unimported template residue; the "
            "credit for the template lives in THIRD_PARTY_NOTICES.md now.")


def test_no_third_party_cdn_url_survives_in_the_dashboard_source():
    hits = []
    for root, dirs, files in os.walk(_DASHBOARD_APP):
        dirs[:] = [d for d in dirs if d not in ("node_modules", ".next")]
        for name in files:
            if not name.endswith((".ts", ".tsx", ".js", ".jsx", ".css")):
                continue
            path = os.path.join(root, name)
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                text = fh.read()
            if "justboil" in text.lower():
                hits.append(os.path.relpath(path, _REPO_ROOT))
    assert hits == [], (
        "dashboard/app still references justboil: "
        f"{hits}. A skeptic greps the tree before reading the import graph.")
