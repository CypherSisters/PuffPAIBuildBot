"""Public-repo hygiene guard for BuildBot's docs and GitHub templates.

CONTRIBUTING.md used to publish a raw security-report email address and a
literal YOUR_USERNAME placeholder in the fork-clone command, and the repo
shipped without issue/PR templates. This is a cheap, standing guard so none
of that regresses: no ``@`` email pattern in CONTRIBUTING.md/SECURITY.md,
no leftover placeholder token (``YOUR_USERNAME``, ``yourusername``) and no
real home-folder path (anything under ``C:\\Users\\`` or ``/home/`` other
than the ``you`` placeholder) anywhere in README.md, docs/, or
CONTRIBUTING.md, and the three GitHub template files exist.

Words that must never ship but cannot be written here either (people's names,
private repo or machine names) live in a list kept OUTSIDE the repository:
set ``PUFFPAI_PRIVATE_WORDS`` to that file (one word per line) and the
private-word guards below read it. Without it they are skipped.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PLACEHOLDER_RE = re.compile(
    r"YOUR_USERNAME|yourusername"
    r"|[A-Za-z]:[\\/]+Users[\\/]+(?!you\b|<)[A-Za-z0-9._-]+"
    r"|/home/(?!you\b|<)[a-z0-9._-]+",
    re.IGNORECASE,
)


def _private_words() -> list[str]:
    """The out-of-tree word list named by PUFFPAI_PRIVATE_WORDS, or []."""
    path = os.environ.get("PUFFPAI_PRIVATE_WORDS", "")
    if not path or not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8") as fh:
        return [w.strip() for w in fh if w.strip() and not w.startswith("#")]


def _private_word_re() -> re.Pattern[str] | None:
    words = _private_words()
    if not words:
        return None
    return re.compile(
        r"\b(?:" + "|".join(re.escape(w) for w in words) + r")\b", re.IGNORECASE)


def _root_docs() -> list[Path]:
    files = []
    for name in ("CONTRIBUTING.md", "SECURITY.md"):
        p = REPO_ROOT / name
        if p.exists():
            files.append(p)
    return files


def _readme_and_docs() -> list[Path]:
    files = []
    readme = REPO_ROOT / "README.md"
    if readme.exists():
        files.append(readme)
    contributing = REPO_ROOT / "CONTRIBUTING.md"
    if contributing.exists():
        files.append(contributing)
    docs_dir = REPO_ROOT / "docs"
    if docs_dir.exists():
        files.extend(sorted(docs_dir.rglob("*.md")))
    return files


@pytest.mark.parametrize("path", _root_docs(), ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_no_email_addresses(path: Path) -> None:
    # Scoped to CONTRIBUTING.md/SECURITY.md (the files that actually leaked
    # a report-by-email address). docs/ is excluded here because ops docs
    # can legitimately contain user@host systemd-unit / service identifiers
    # that match an email-shaped regex but are not email addresses.
    text = path.read_text(encoding="utf-8")
    hits = EMAIL_RE.findall(text)
    assert not hits, f"{path} contains email address(es): {hits}"


@pytest.mark.parametrize("path", _readme_and_docs(), ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_no_placeholder_tokens(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    hits = PLACEHOLDER_RE.findall(text)
    private = _private_word_re()
    if private is not None:
        hits += private.findall(text)
    assert not hits, f"{path} contains placeholder token(s): {hits}"


def test_target_files_found() -> None:
    """Guard against the parametrize silently collecting zero files."""
    assert (REPO_ROOT / "CONTRIBUTING.md").exists()
    assert (REPO_ROOT / "SECURITY.md").exists()
    assert (REPO_ROOT / "README.md").exists()


# ── W4: the same rule, over the whole tracked tree ───────────────────────────
#
# The guard above was scoped to two files, so `setup.py` shipped
# `author_email="team@..."` right past it while SECURITY.md said reports go
# through GitHub and not an inbox. Scoping a guard to the file that broke it
# catches that file; it does not catch the class. This scans everything git
# actually tracks — that being the definition of "in the public repo" — and
# fails on any contactable address anywhere in it.

#: Domains that are reserved-by-standard or non-routable, and therefore cannot
#: be a real contact address. RFC 2606/6761 reserves example.*, .example,
#: .invalid, .test and .localhost for documentation and testing; .local is
#: mDNS. Test fixtures and UI placeholders live here on purpose.
_NON_ROUTABLE_RE = re.compile(
    r"@(?:[A-Za-z0-9.-]+\.)?example\.(?:com|org|net)$|"
    r"@[A-Za-z0-9.-]*\.(?:example|invalid|test|localhost|local)$",
    re.IGNORECASE,
)

#: Files whose "@" strings are transport syntax, not contact addresses.
_EMAIL_SCAN_SKIP = {
    "LICENSE",                     # credit line; carries no address anyway
    "dashboard/package-lock.json",  # npm records `git@github.com:` SSH URLs
    "dashboard/yarn.lock",
    "package-lock.json",
    "config/wordlists/NOTICE.txt",  # SCOWL/UKACD licence text, kept verbatim (third-party authors)
}

#: Only text this guard can meaningfully read. Binary assets (images,
#: models) contain byte sequences that match an email regex by coincidence.
_EMAIL_SCAN_SUFFIXES = {
    ".py", ".pyi", ".md", ".txt", ".json", ".toml", ".cfg", ".ini", ".yml",
    ".yaml", ".ts", ".tsx", ".js", ".jsx", ".css", ".html", ".sh", ".in",
}


def _tracked_text_files() -> list[Path]:
    """Every git-tracked text file, minus the documented exceptions."""
    import subprocess

    try:
        out = subprocess.run(
            ["git", "ls-files", "-z"], cwd=REPO_ROOT, capture_output=True,
            text=True, check=True, timeout=60).stdout
    except Exception:  # not a git checkout (a source tarball, say)
        return []
    files = []
    self_rel = Path(__file__).resolve().relative_to(REPO_ROOT).as_posix()
    for rel in out.split("\0"):
        if not rel or rel in _EMAIL_SCAN_SKIP or rel == self_rel:
            continue  # this guard names the patterns it looks for on purpose
        path = REPO_ROOT / rel
        if path.suffix.lower() not in _EMAIL_SCAN_SUFFIXES:
            continue
        if not path.is_file():
            continue  # a submodule, or deleted-but-still-indexed
        files.append(path)
    return files


def test_no_contact_email_anywhere_in_the_tracked_tree() -> None:
    """No routable email address in any tracked text file.

    setup.py's `author_email` is the specific regression: SECURITY.md promises
    reports go through GitHub, and a packaged inbox address contradicts it in a
    file nobody re-reads. Reserved documentation domains (example.com, .local,
    .invalid) are allowed — they are placeholders by standard, not addresses.
    """
    offenders = []
    for path in _tracked_text_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for i, line in enumerate(text.splitlines(), 1):
            for hit in EMAIL_RE.findall(line):
                if _NON_ROUTABLE_RE.search(hit):
                    continue
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{i}: {hit}")
    assert not offenders, (
        "contact email address(es) in the public repo — route contact through "
        "the GitHub org instead:\n  " + "\n  ".join(offenders))


def test_email_scan_actually_covers_the_tree() -> None:
    """Guard against the scan silently collecting nothing (or almost nothing).

    A guard that walks zero files passes forever. The files named here are the
    ones W4 was about; the count check catches a broken `git ls-files`.
    """
    scanned = {p.relative_to(REPO_ROOT).as_posix() for p in _tracked_text_files()}
    if not scanned:
        pytest.skip("not a git checkout; tree-wide email scan unavailable")
    assert len(scanned) > 100, f"tree scan collected only {len(scanned)} files"
    for required in ("setup.py", "SECURITY.md", "README.md",
                     "dashboard/package.json"):
        assert required in scanned, f"{required} is not covered by the scan"


def test_setup_py_declares_no_author_email() -> None:
    """The exact line W4 named."""
    text = (REPO_ROOT / "setup.py").read_text(encoding="utf-8")
    assert "author_email" not in text, (
        "setup.py declares author_email; SECURITY.md routes reports through "
        "GitHub, not an inbox")




def _source_and_test_files() -> list[Path]:
    files = []
    for sub in ("src", "tests"):
        d = REPO_ROOT / sub
        if d.exists():
            files.extend(sorted(p for p in d.rglob("*.py")))
    return files


def test_no_founder_names_in_source_or_tests() -> None:
    """Authorship credit belongs in LICENSE and README, as "Cypher Sisters".

    No person's name may leak into code, comments, docstrings or test fixtures.
    Fixtures use fake identities ("Test Fakeperson"), never a real person's
    name. The names themselves come from the out-of-tree PUFFPAI_PRIVATE_WORDS
    list, so this file does not publish them.
    """
    private = _private_word_re()
    if private is None:
        pytest.skip("PUFFPAI_PRIVATE_WORDS is not set")
    offenders = []
    for path in _source_and_test_files():
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if private.search(line):
                rel = path.relative_to(REPO_ROOT)
                offenders.append(f"{rel}:{i}: {line.strip()}")
    assert not offenders, "founder names in source/tests:\n" + "\n".join(offenders)


@pytest.mark.parametrize(
    "path",
    [
        REPO_ROOT / ".github" / "ISSUE_TEMPLATE" / "bug_report.md",
        REPO_ROOT / ".github" / "ISSUE_TEMPLATE" / "wizard_gap.md",
        REPO_ROOT / ".github" / "ISSUE_TEMPLATE" / "config.yml",
        REPO_ROOT / ".github" / "PULL_REQUEST_TEMPLATE.md",
    ],
    ids=lambda p: str(p.relative_to(REPO_ROOT)),
)
def test_github_templates_exist(path: Path) -> None:
    assert path.exists(), f"missing GitHub template: {path}"
