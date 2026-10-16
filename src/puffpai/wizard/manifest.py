"""Validation of one wizard package manifest: ``puffpai_package/v1``.

A port of the HomeBot skills validator (``src/puffpai/skills/manifest.py``),
kept to the same doctrine: **every rule here is a refusal rule**. There is no
partially-valid package, because a partially-valid package is one whose
permissions were read from a file the validator had already stopped trusting.

Three rules carry the weight:

* **One writer.** ``writes`` names exactly one manifest section. Two sections is
  a refusal, not a merge. This is the rule the whole re-housing exists for:
  without it, fourteen steps wrote one record with no owner.
* **Reserved sections.** :data:`RESERVED_SECTIONS` may only be written by a
  package that shipped inside this installed build. "Built-in" is decided by
  the loader from WHERE the folder is (:mod:`puffpai.wizard.loader`), never from
  a field the manifest claims -- a manifest that claims ``builtin: true`` from
  outside the shipped directory is refused for the claim itself.
* **No credentials.** ``secrets`` must be empty in v1. The broker credential
  path is unbuilt; a validator that accepted a handle today would be
  promising an injection path that does not exist.

Failures raise :class:`PackageError`, which carries a sentence a stranger can
read. The runner turns that into a refusal line and a gap; it never reaches the
owner as a traceback.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import re
from typing import Any

SCHEMA = "puffpai_package/v1"

ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,31}$")
DOOR_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+){0,3}$")
VERSION_RE = re.compile(r"^\d{1,3}\.\d{1,3}\.\d{1,3}$")
_TAG_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+){0,2}$")
_HOST_LABEL_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")

MAX_MANIFEST_BYTES = 8 * 1024
MAX_LABEL_LEN = 80
MAX_DESCRIPTION_LEN = 300
MAX_HOSTS = 16
MAX_DOORS = 8
MAX_TAGS = 12

#: Ceilings a manifest may go under and never over. The runner clamps to these
#: as well, so a manifest that lied about a limit still cannot exceed it.
MAX_TIMEOUT_S = 60
MAX_REQUESTS = 8
MAX_RESPONSE_BYTES = 512 * 1024

DEFAULT_LIMITS = {"timeout_s": 20, "max_requests": 4,
                  "max_response_bytes": 256 * 1024}

#: v1 has one tier: built-in. ``owner`` is kept because a household that writes
#: its own package is not a third party. There is no signature field and no
#: submission path yet.
ALLOWED_AUTHORS = ("puffpai", "owner")

KINDS = ("skill", "agent", "app")
EGRESS_MODES = ("none", "build_only", "runtime")
RERUN_MODES = ("idempotent", "additive", "destructive")
UNDO_MODES = ("reversible", "reversible_with_residue", "not_reversible")
ESTIMATE_BASES = ("measured:this_machine", "calibrated:sample", "unknown")

#: Every section of the build record a package may name in ``reads``/``writes``.
#: An unenumerated section is a refusal: a package that reads a section nobody
#: declared is a package reading whatever happens to be in the record.
KNOWN_SECTIONS = (
    "run", "machine", "persona", "brain", "sources", "tools", "knowledge",
    "context", "crew", "posture", "egress", "surfaces", "assemble", "prove", "handoff",
)

#: Writable only by built-in packages, refused at validate
#: rather than at run. These are where the build's safety and its proof live.
RESERVED_SECTIONS = ("run", "posture", "egress", "brain", "prove")

#: The runner owns ``run``. No package writes it, built-in or otherwise.
RUNNER_ONLY_SECTIONS = ("run",)

#: Only these two may ship ``blocking: true``.
BLOCKING_ALLOWED_IDS = ("brain", "secure")

#: Files a package folder may contain. ``card.json`` is optional in Phase 1:
#: there are no cards yet, because today's steps prompt inline (spec F).
PACKAGE_FILES = ("manifest.json", "skill.py", "card.json")
REQUIRED_PACKAGE_FILES = ("manifest.json",)
ALLOWED_SUBDIRS = ("data",)

_ALLOWED_KEYS = frozenset({
    "schema", "id", "kind", "version", "author", "license", "label",
    "description", "reads", "writes", "permissions", "doors", "secrets",
    "egress", "estimate", "rerun", "undo", "blocking", "provides", "needs",
    "limits", "builtin",
})

_PERMISSION_KEYS = frozenset({"network_hosts", "files", "ha_domains", "secrets"})


class PackageError(ValueError):
    """A package this build will not accept, with the reason in plain words."""


def _ascii_text(value: Any, limit: int) -> bool:
    return (isinstance(value, str) and value.strip() != ""
            and len(value) <= limit and value.isascii() and value.isprintable())


def is_registrable_hostname(host: Any) -> bool:
    """True for a plain registrable NAME -- never an address, wildcard or port.

    Written here rather than imported: ``security/egress_doors`` in this tree has
    no such helper, and a door host check that silently fell back to "anything"
    on an import error would be the worst kind of pass.
    """
    if not isinstance(host, str):
        return False
    name = host.strip().rstrip(".").lower()
    if not name or len(name) > 253 or not name.isascii():
        return False
    if any(ch in name for ch in (":", "/", "\\", "*", "?", "@", "[", "]", "_", " ")):
        return False
    labels = name.split(".")
    if len(labels) < 2:
        return False
    if not all(_HOST_LABEL_RE.match(label) for label in labels):
        return False
    tld = labels[-1]
    return len(tld) >= 2 and tld.isalpha()


def _string_list(value: Any, field: str, package_id: str, *, limit: int) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > limit:
        raise PackageError(
            f"Package '{package_id}': {field} must be a list of at most "
            f"{limit} entries.")
    out: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise PackageError(
                f"Package '{package_id}': {field} contains {item!r}, which is "
                f"not a name.")
        if item not in out:
            out.append(item)
    return out


def _validate_permissions(block: Any, package_id: str) -> dict:
    if not isinstance(block, dict):
        raise PackageError(
            f"Package '{package_id}' does not declare a permissions block. A "
            f"package with unstated reach is refused.")
    unknown = set(block) - _PERMISSION_KEYS
    if unknown:
        raise PackageError(
            f"Package '{package_id}' declares unknown permissions "
            f"{sorted(unknown)!r}; this build reads network_hosts, files, "
            f"ha_domains and secrets.")

    hosts = block.get("network_hosts", [])
    if not isinstance(hosts, list):
        raise PackageError(f"Package '{package_id}': network_hosts is not a list.")
    if len(hosts) > MAX_HOSTS:
        raise PackageError(
            f"Package '{package_id}' names {len(hosts)} hosts; {MAX_HOSTS} is "
            f"the most one package may reach.")
    clean_hosts: list[str] = []
    for raw in hosts:
        if not is_registrable_hostname(raw):
            raise PackageError(
                f"Package '{package_id}' names {raw!r} as a host. A door host "
                f"is a registrable NAME -- never an address, a wildcard, a "
                f"scheme or a port.")
        host = raw.strip().rstrip(".").lower()
        if host not in clean_hosts:
            clean_hosts.append(host)

    files = block.get("files", [])
    if not isinstance(files, list) or not set(map(str, files)) <= set(ALLOWED_SUBDIRS):
        raise PackageError(
            f"Package '{package_id}' asks for files {files!r}. The only path a "
            f"package may write is its own 'data' directory.")

    for key in ("ha_domains", "secrets"):
        value = block.get(key, [])
        if not isinstance(value, list) or value:
            raise PackageError(
                f"Package '{package_id}' declares permissions.{key}={value!r}. "
                f"v1 packages touch neither Home Assistant nor the vault: there "
                f"is no credential path yet, and a validator that accepted a "
                f"handle would be promising one.")

    return {"network_hosts": clean_hosts, "files": [str(f) for f in files],
            "ha_domains": [], "secrets": []}


def _validate_limits(block: Any, package_id: str) -> dict:
    if block is None:
        return dict(DEFAULT_LIMITS)
    if not isinstance(block, dict):
        raise PackageError(f"Package '{package_id}': limits is not an object.")
    ceilings = {"timeout_s": MAX_TIMEOUT_S, "max_requests": MAX_REQUESTS,
                "max_response_bytes": MAX_RESPONSE_BYTES}
    out = dict(DEFAULT_LIMITS)
    for key, value in block.items():
        if key not in ceilings:
            raise PackageError(
                f"Package '{package_id}' declares unknown limit {key!r}.")
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise PackageError(
                f"Package '{package_id}': limit {key} must be a positive integer.")
        if value > ceilings[key]:
            raise PackageError(
                f"Package '{package_id}': limit {key}={value} is over the "
                f"ceiling {ceilings[key]}.")
        out[key] = value
    return out


def _validate_estimate(block: Any, package_id: str) -> dict:
    if block is None:
        return {"bytes": 0, "seconds": 0, "basis": "unknown"}
    if not isinstance(block, dict) or set(block) - {"bytes", "seconds", "basis"}:
        raise PackageError(
            f"Package '{package_id}': estimate takes bytes, seconds and basis.")
    for key in ("bytes", "seconds"):
        value = block.get(key, 0)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise PackageError(
                f"Package '{package_id}': estimate.{key} must be a whole "
                f"number of {key}, or 0.")
    basis = block.get("basis", "unknown")
    if basis not in ESTIMATE_BASES:
        raise PackageError(
            f"Package '{package_id}': estimate.basis {basis!r} is not one of "
            f"{', '.join(ESTIMATE_BASES)}. An invented number is a defect.")
    return {"bytes": int(block.get("bytes", 0)),
            "seconds": int(block.get("seconds", 0)), "basis": basis}


def _writes_section(value: Any, package_id: str) -> str:
    """The one section this package owns. Two is a refusal, zero is a refusal."""
    if isinstance(value, str):
        section = value
    elif isinstance(value, list):
        if len(value) != 1:
            raise PackageError(
                f"Package '{package_id}' says it writes {value!r}. A package "
                f"writes exactly one section of the build record -- that is the "
                f"rule the whole pipeline rests on, and two owners for one "
                f"section is how a build silently overwrites itself.")
        section = value[0]
    else:
        raise PackageError(
            f"Package '{package_id}' does not name the one section it writes.")
    if not isinstance(section, str) or section not in KNOWN_SECTIONS:
        raise PackageError(
            f"Package '{package_id}' writes {section!r}, which is not a section "
            f"of the build record ({', '.join(KNOWN_SECTIONS)}).")
    return section


def validate(data: Any, *, builtin: bool) -> dict:
    """Return the manifest in canonical form, or raise :class:`PackageError`.

    *builtin* is the loader's finding about WHERE this package was read from.
    It is never taken from the manifest; a manifest that claims it from outside
    the shipped directory is refused for the claim.
    """
    if not isinstance(data, dict):
        raise PackageError("The manifest is not a JSON object.")

    unknown = set(data) - _ALLOWED_KEYS
    if unknown:
        raise PackageError(
            f"The manifest declares unknown keys {sorted(unknown)!r}. This "
            f"build reads only the fields it can enforce; anything else is a "
            f"claim nothing checks.")

    if str(data.get("schema") or "") != SCHEMA:
        raise PackageError(
            f"The manifest says schema {data.get('schema')!r}; this build "
            f"reads {SCHEMA!r}.")

    package_id = data.get("id")
    if not isinstance(package_id, str) or not ID_RE.match(package_id):
        raise PackageError(
            f"Package id {package_id!r} is not usable: 2 to 32 characters, "
            f"lowercase, starting with a letter, letters/digits/underscore.")
    try:
        from puffpai import persona_scaffold
    except Exception as exc:  # pragma: no cover - ships together
        raise PackageError(
            "The reserved-id list could not be read, so no package can be "
            "checked against it. Nothing is installed until it can.") from exc
    if persona_scaffold.is_reserved_id(package_id):
        raise PackageError(
            f"Package id {package_id!r} is a word this build already uses for "
            f"something else. A collision an owner cannot see is a collision "
            f"they cannot fix.")

    claimed_builtin = data.get("builtin")
    if claimed_builtin is not None:
        if not isinstance(claimed_builtin, bool):
            raise PackageError(
                f"Package '{package_id}': builtin must be true or false.")
        if claimed_builtin and not builtin:
            raise PackageError(
                f"Package '{package_id}' calls itself built-in. Built-in means "
                f"it shipped inside this install, which is decided by where the "
                f"folder is and never by what the folder says.")
        if builtin and claimed_builtin is False:
            raise PackageError(
                f"Package '{package_id}' shipped with this build but declares "
                f"builtin: false. A package does not get to disown where it "
                f"came from.")

    kind = data.get("kind")
    if kind not in KINDS:
        raise PackageError(
            f"Package '{package_id}' is kind {kind!r}; this build reads "
            f"{', '.join(KINDS)}.")

    version = data.get("version")
    if not isinstance(version, str) or not VERSION_RE.match(version):
        raise PackageError(
            f"Package '{package_id}' has version {version!r}; it should read "
            f"like 1.0.0.")

    author = data.get("author")
    if author not in ALLOWED_AUTHORS:
        raise PackageError(
            f"Package '{package_id}' names author {author!r}. This build runs "
            f"only {' or '.join(ALLOWED_AUTHORS)} packages; a third-party "
            f"package needs a signature this build cannot check yet.")

    if not _ascii_text(data.get("license"), 40):
        raise PackageError(
            f"Package '{package_id}' names no license. Every package says what "
            f"it is licensed under, including ours.")
    if not _ascii_text(data.get("label"), MAX_LABEL_LEN):
        raise PackageError(
            f"Package '{package_id}' has no label a person could read.")
    description = data.get("description", "")
    if description and not _ascii_text(description, MAX_DESCRIPTION_LEN):
        raise PackageError(f"Package '{package_id}' has an unusable description.")

    writes = _writes_section(data.get("writes"), package_id)
    if writes in RUNNER_ONLY_SECTIONS:
        raise PackageError(
            f"Package '{package_id}' writes the '{writes}' section. That "
            f"section is the runner's own record of what happened; no package "
            f"writes it, built-in or not.")
    if writes in RESERVED_SECTIONS and not builtin:
        raise PackageError(
            f"Package '{package_id}' writes the reserved section '{writes}'. "
            f"The sections that hold this build's safety and its proof "
            f"({', '.join(RESERVED_SECTIONS)}) are written only by packages "
            f"that shipped with the build.")

    reads = _string_list(data.get("reads"), "reads", package_id, limit=len(KNOWN_SECTIONS))
    for section in reads:
        if section not in KNOWN_SECTIONS:
            raise PackageError(
                f"Package '{package_id}' reads {section!r}, which is not a "
                f"section of the build record.")

    permissions = _validate_permissions(data.get("permissions"), package_id)

    doors = _string_list(data.get("doors"), "doors", package_id, limit=MAX_DOORS)
    for door_id in doors:
        if not DOOR_RE.match(door_id):
            raise PackageError(
                f"Package '{package_id}' names the door {door_id!r}, which is "
                f"not a door id.")

    secrets = data.get("secrets", [])
    if not isinstance(secrets, list) or secrets:
        raise PackageError(
            f"Package '{package_id}' declares secrets={secrets!r}. v1 records a "
            f"keyed API as declared with no key path yet; there is nowhere safe "
            f"to put a value, so no package may ask for one.")

    egress = data.get("egress", "none")
    if egress not in EGRESS_MODES:
        raise PackageError(
            f"Package '{package_id}': egress {egress!r} is not one of "
            f"{', '.join(EGRESS_MODES)}.")
    if egress == "none" and permissions["network_hosts"]:
        raise PackageError(
            f"Package '{package_id}' says it needs no network and then names "
            f"hosts. One of the two is wrong, and the safe reading is the one "
            f"that refuses.")

    rerun = data.get("rerun", "idempotent")
    if rerun not in RERUN_MODES:
        raise PackageError(
            f"Package '{package_id}': rerun {rerun!r} is not one of "
            f"{', '.join(RERUN_MODES)}.")

    undo = data.get("undo")
    if kind == "skill" and undo not in UNDO_MODES:
        raise PackageError(
            f"Package '{package_id}': undo {undo!r} is not one of "
            f"{', '.join(UNDO_MODES)}. A skill says out loud what re-running it "
            f"can and cannot put back.")

    blocking = data.get("blocking", False)
    if not isinstance(blocking, bool):
        raise PackageError(f"Package '{package_id}': blocking must be true or false.")
    if blocking and package_id not in BLOCKING_ALLOWED_IDS:
        raise PackageError(
            f"Package '{package_id}' declares itself blocking. Only "
            f"{' and '.join(BLOCKING_ALLOWED_IDS)} may stop a build; every "
            f"other failure records a gap and the build carries on.")

    provides = _string_list(data.get("provides"), "provides", package_id, limit=MAX_TAGS)
    needs = _string_list(data.get("needs"), "needs", package_id, limit=MAX_TAGS)
    for tag in (*provides, *needs):
        if not _TAG_RE.match(tag):
            raise PackageError(
                f"Package '{package_id}' names the capability tag {tag!r}, "
                f"which is not a tag.")

    limits = _validate_limits(data.get("limits"), package_id)
    estimate = _validate_estimate(data.get("estimate"), package_id)

    return {
        "schema": SCHEMA,
        "id": package_id,
        "kind": kind,
        "version": version,
        "author": author,
        "license": data["license"],
        "label": data["label"],
        "description": description,
        "reads": reads,
        "writes": writes,
        "permissions": permissions,
        "doors": doors,
        "secrets": [],
        "egress": egress,
        "estimate": estimate,
        "rerun": rerun,
        "undo": undo if kind == "skill" else None,
        "blocking": blocking,
        "provides": provides,
        "needs": needs,
        "limits": limits,
        "builtin": bool(builtin),
    }


__all__ = [
    "ALLOWED_AUTHORS", "ALLOWED_SUBDIRS", "BLOCKING_ALLOWED_IDS",
    "DEFAULT_LIMITS", "EGRESS_MODES", "ESTIMATE_BASES", "ID_RE", "KINDS",
    "KNOWN_SECTIONS", "MAX_HOSTS", "MAX_MANIFEST_BYTES", "MAX_REQUESTS",
    "MAX_RESPONSE_BYTES", "MAX_TIMEOUT_S", "PACKAGE_FILES", "PackageError",
    "REQUIRED_PACKAGE_FILES", "RERUN_MODES", "RESERVED_SECTIONS",
    "RUNNER_ONLY_SECTIONS", "SCHEMA", "UNDO_MODES", "is_registrable_hostname",
    "validate",
]
