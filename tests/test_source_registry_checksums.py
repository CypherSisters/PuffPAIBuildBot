"""W7 — the source registry's own checksum rule, enforced.

``config/source_registry.json`` IS the allowlist: adding a row grants a network
permission. Its maintainer note says a ``corpus`` row names one fixed file and
must carry a ``sha256``, and that a URL resolving through a moving branch name
cannot be pinned at all and therefore does not belong. Both rules were stated
and neither was checked, and one row broke both: ``hyg_star_database`` fetched
``raw.githubusercontent.com/astronexus/HYG-Database/main/...`` with no digest,
so anyone able to push to that branch could land an arbitrary 35 MB file on any
install through the wizard's own sourcing step.

The row is gone. These tests are what stops the next one.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
REGISTRY = REPO_ROOT / "config" / "source_registry.json"

#: Kinds whose rows download one fixed file and must therefore pin a digest.
#: ``zim`` is exempt (Kiwix reissues its files on its own schedule) and
#: ``docs`` is exempt (a landing page, not a download).
CHECKSUM_REQUIRED_KINDS = ("corpus",)

#: Path segments that mean "whatever is there today". A URL containing one
#: cannot be pinned, because the bytes behind it change without the URL doing.
MUTABLE_REF_RE = re.compile(r"/(main|master|latest|HEAD|current|tip)/",
                            re.IGNORECASE)

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _rows() -> list[dict]:
    data = json.loads(REGISTRY.read_text(encoding="utf-8"))
    return list(data.get("sources") or [])


def test_registry_has_rows() -> None:
    """Guard against every check below passing on an empty list."""
    rows = _rows()
    assert len(rows) >= 5, f"source registry looks empty or truncated: {len(rows)}"


def test_every_url_row_makes_an_explicit_integrity_claim() -> None:
    """No row is silent about its digest. Either it pins one or it says why not.

    The first version of this guard exempted ``zim`` and ``docs`` and required a
    digest on ``corpus`` -- and there are no corpus rows, so it asserted nothing
    at all while reading as though the registry were checked. A rule that is
    vacuous is worse than no rule: it is a rule someone will cite.

    Now every row with a URL must carry a measured ``sha256`` OR
    ``pin: "none"`` with a written ``pin_reason``. A new row cannot be added
    without a human typing one or the other.
    """
    offenders = []
    for row in _rows():
        if row.get("kiwix_book") and not row.get("source_url"):
            # A Kiwix book row: its claim is the library's own .meta4 SHA-256,
            # read and checked at download time. It must say so.
            if row.get("pin") != "kiwix_meta4" or                     len(str(row.get("pin_reason") or "").split()) < 8:
                offenders.append(f"{row.get('id')}: a Kiwix row without "
                                 f"pin: kiwix_meta4 and a written reason")
            continue
        if not row.get("source_url"):
            offenders.append(f"{row.get('id')}: neither a URL nor a Kiwix book")
            continue
        digest = row.get("sha256")
        if isinstance(digest, str) and SHA256_RE.match(digest.lower()):
            continue
        if row.get("pin") == "none" and str(row.get("pin_reason") or "").strip():
            continue
        offenders.append(f"{row.get('id')} (kind={row.get('kind')}): "
                         f"sha256={digest!r} pin={row.get('pin')!r} "
                         f"pin_reason={row.get('pin_reason')!r}")
    assert not offenders, (
        "source_registry.json rows that neither pin a sha256 nor declare "
        "pin: 'none' with a reason:\n  " + "\n  ".join(offenders))


def test_the_claim_is_actually_exercised() -> None:
    """Anti-vacuity: the rule above must be judging real rows, not zero rows.

    This is the check the first version of this file did not have, and its
    absence is the whole reason the guard passed while testing nothing.
    """
    judged = [r for r in _rows() if r.get("source_url") or r.get("kiwix_book")]
    assert len(judged) >= 5, f"only {len(judged)} rows name a download"
    claims = {("sha256" if r.get("sha256") else r.get("pin")) for r in judged}
    assert claims and "none" not in claims, (
        f"every download must be checked against a digest; claims: {claims}")


def test_a_corpus_row_may_not_decline_to_pin() -> None:
    """``pin: none`` is available to zim and docs rows, never to corpus.

    A corpus row names one fixed file at one address. If it cannot be hashed,
    it does not belong in the allowlist -- which is exactly the judgement that
    removed hyg_star_database.
    """
    offenders = [r.get("id") for r in _rows()
                 if r.get("kind") == "corpus" and r.get("pin") == "none"]
    assert not offenders, (
        f"corpus rows declining to pin a digest: {offenders}. A corpus row "
        f"names one fixed file; if it cannot be hashed it does not go in.")


def test_pin_reasons_are_written_for_a_person() -> None:
    """A reason field that says 'n/a' is silence with extra steps."""
    lazy = [r.get("id") for r in _rows()
            if r.get("pin") == "none"
            and len(str(r.get("pin_reason") or "").split()) < 8]
    assert not lazy, f"rows with a token pin_reason: {lazy}"


def test_the_registry_note_says_how_downloads_are_checked() -> None:
    """Every row is checked against the library's own SHA-256.

    The note used to say nothing was verified, which was true. It must now say
    how the check works, and it must not keep the old sentence.
    """
    note = json.loads(REGISTRY.read_text(encoding="utf-8"))["_maintainer_note"]
    assert "nothing downloaded through this registry is currently verified" not in note
    assert "kiwix_meta4" in note and "SHA-256" in note


def test_no_row_fetches_through_a_mutable_ref() -> None:
    """No row's URL resolves through a moving branch or 'latest' pointer.

    This is the specific shape W7 found. ``.../HYG-Database/main/...`` served
    whatever the branch tip held at download time, so pinning a digest was not
    merely omitted -- it was impossible. Name an immutable tag or commit.
    """
    offenders = []
    for row in _rows():
        url = row.get("source_url") or ""
        if MUTABLE_REF_RE.search(url):
            offenders.append(f"{row.get('id')}: {url}")
    assert not offenders, (
        "source_registry.json rows fetching through a mutable ref (pin a tag "
        "or commit instead):\n  " + "\n  ".join(offenders))


def test_the_hyg_row_is_gone() -> None:
    """The exact row W7 named does not come back unpinned."""
    ids = {row.get("id") for row in _rows()}
    assert "hyg_star_database" not in ids, (
        "hyg_star_database is back. It may only return with an immutable "
        "tag/commit in source_url and a maintainer-measured sha256.")


@pytest.mark.parametrize("kind", CHECKSUM_REQUIRED_KINDS)
def test_checksum_required_kinds_are_real_kinds(kind: str) -> None:
    """The rule above must name kinds the loader actually recognises.

    A typo here would silently exempt everything, which is the failure mode a
    guard like this is most likely to have.
    """
    from puffpai import source_registry

    assert kind in source_registry.KINDS
