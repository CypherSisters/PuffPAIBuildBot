"""The build manifest — the declarative record of what this box became.

A build IS a manifest plus overlays. The wizard's whole job is to produce this
file: a human-readable, versioned record of the hardware census, the chosen
brain, the crew budget, storage and knowledge locations, the egress posture, and
the build's identity. Applying a manifest is what turns BuildBot into a build;
publishing one (HomeBot's, say) is what makes a build shareable.

Two rules give the manifest its integrity:

* **Contracts are not configurable.** The encrypted vault, the human-only
  consent gate, the action-boundary gates, and zero-telemetry are invariants
  every build inherits. No manifest field can weaken them. The ``contracts``
  block records them so a manifest is self-describing, but it is a statement, not
  a set of switches, and :func:`validate_manifest` reports (and callers strip)
  any smuggled field that tries to turn one off.
* **No field is an instruction.** Nothing in the manifest is ever concatenated
  into an agent prompt. Free-text the owner types (the bot's name, a build goal)
  is stored as data and used only as data — labels and keyword matches — never as
  a directive. Community-contributed items are untrusted the same way.

Schema v2 adds three blocks to v1's shape, additively, so v1 manifests still
read and a re-run simply fills the new ones in:

* ``posture`` - the two axes from :mod:`puffpai.build_posture`: whether the
  wizard could reach the network while building, what the finished bot may
  reach when it runs, and whether each was chosen or defaulted.
* ``surfaces`` - the allowlist derived from the build goal. Surfaces outside it
  are not loaded, so an empty allowlist fails closed rather than open.
* ``provenance`` - where this build came from; a sneakernet import
  (:mod:`puffpai.build_bundle`) records the bundle it was applied from.

None of the three is a back door: the contract guard covers their vocabulary,
and an unrecognised posture value is coerced to the restrictive reading rather
than honoured.

The wizard never overwrites blindly: it reads any existing manifest, diffs the
new one against it (:func:`diff_manifest`), shows what would change, and writes
only on a yes.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Optional

from puffpai import config_health

SCHEMA = "build_manifest/v2"

# v2 adds three blocks - posture, surfaces, provenance - to v1's shape. Nothing
# was removed or renamed, so a v1 manifest still reads: every accessor tolerates
# the missing blocks, and re-writing one simply fills them in. A schema we do not
# know is a warning, never a refusal to read.
SUPPORTED_SCHEMAS = ("build_manifest/v1", "build_manifest/v2")

# The contracts, stated once. These are invariants, not settings — recorded so a
# manifest describes its own guarantees, never read back as toggles.
_CONTRACTS: dict[str, Any] = {
    "vault": "Encrypted, PIN/MFA-gated. Even the AI must ask to read it.",
    "consent_gate": "Human-only. No agent can grant consent on a person's behalf.",
    "action_gates": "On at the action boundary. Reach-into-the-world actions are gated.",
    "telemetry": "None. No phone-home; every egress is owner-invoked and enumerated.",
    "_note": (
        "These are contracts, not configuration. No manifest field disables "
        "them; the wizard and the loader never read this block as switches."
    ),
}

# Defense in depth for the contract rule: if a hand-edited manifest smuggles one
# of these keys in to try to disable a contract, validate_manifest flags it and
# callers drop it. The presence of the key is the smell; we do not honour it.
_FORBIDDEN_KEYS = frozenset({
    "disable_vault",
    "vault_enabled",
    "disable_consent",
    "consent_enabled",
    "disable_gates",
    "action_gates_enabled",
    "disable_sentinel",
    "disable_drawbridge",
    "telemetry_enabled",
    "enable_telemetry",
    "phone_home",
    "allow_prompt_injection",
    # v2's new blocks are new places to hide the same trick. A posture, a surface
    # allowlist, or an imported bundle's provenance must never be able to reach
    # back and switch a contract off, so the guard covers their vocabulary too.
    "disable_egress_gate",
    "egress_gate_enabled",
    "allow_all_egress",
    "egress_allow_all",
    "bypass_egress",
    "disable_action_gates",
    "allow_unattended_actions",
    "bypass_consent",
    "skip_consent",
    "disable_vault",
    "disable_vault_pin",
    "vault_sealed_override",
    "disable_surface_allowlist",
    "load_all_surfaces",
    "allow_surface_bypass",
    "surface_allowlist_enabled",
    "skip_checksum",
    "skip_verification",
    "trust_bundle",
    "allow_unsigned_bundle",
    "disable_sanitize",
    "override_contracts",
    "contracts_enabled",
    "telemetry_endpoint",
    "phone_home_url",
    # The bare word, not only the compounds. "telemetry_enabled": false is
    # obviously a switch and was caught; a plain "telemetry": {...} block sailed
    # through and could sit in a manifest looking like configuration this build
    # honours. It never was, but a manifest must not be able to imply it. The
    # contracts block owns the word, and that block is regenerated from
    # _CONTRACTS on every sanitize, so stripping it here costs nothing.
    "telemetry",
})

# Not contract switches - secrets. Nothing in this list belongs in a manifest at
# all, not even hashed, so they are stripped on the same pass. The vault records
# that it exists and is sealed; it never records the way in.
_SECRET_KEYS = frozenset({
    "vault_pin",
    "vault_pin_hash",
    "vault_recovery_code",
    "vault_recovery_hash",
    "recovery_code",
    "pin",
    "pin_hash",
    "ha_token",
    "api_key",
    "api_token",
    "password",
    "secret",
})

# Everything sanitize_manifest drops, wherever it appears.
_STRIPPED_KEYS = _FORBIDDEN_KEYS | _SECRET_KEYS


# ---------------------------------------------------------------------------
# paths
# ---------------------------------------------------------------------------

def manifest_path() -> str:
    """Absolute path of this build's manifest (honours PUFFPAI_DATA_DIR)."""
    from puffpai.config import get_config
    return os.path.join(get_config().CONFIG_DIR, "build_manifest.json")


# ---------------------------------------------------------------------------
# assembly
# ---------------------------------------------------------------------------

def assemble_manifest(
    census: dict[str, Any],
    *,
    identity: Optional[dict[str, Any]] = None,
    brain: Optional[dict[str, Any]] = None,
    crew: Optional[dict[str, Any]] = None,
    storage: Optional[dict[str, Any]] = None,
    knowledge: Optional[dict[str, Any]] = None,
    egress: Optional[dict[str, Any]] = None,
    integrations: Optional[dict[str, Any]] = None,
    posture: Optional[dict[str, Any]] = None,
    surfaces: Optional[dict[str, Any]] = None,
    provenance: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Assemble a manifest from whatever wizard steps have run.

    Only the census is required; every other section defaults to a sane empty
    shape, so a manifest can be written after the hardware step and enriched as
    later steps complete. Never raises.

    The v2 blocks default the same way the rest do. ``posture`` defaults to the
    safe skip-this-step answer with both axes marked ``defaulted``, so a manifest
    always says whether the owner chose or the wizard chose. ``surfaces``
    defaults to an empty allowlist, and an empty allowlist means *nothing extra
    is loaded* - the trimming fails closed. ``provenance`` records where the
    build came from; a bundle import fills it in.

    One more block appears only when it has something to say: ``config_health``,
    written when the build was assembled while a first-party config was corrupt
    (see :mod:`puffpai.config_health`). A build should carry the truth about how
    it was made, and "assembled from a two-item fallback catalog" is part of that
    truth. It is a statement, never a setting, and it is regenerated from the
    live record on every write so it cannot be forged either way.
    """
    manifest: dict[str, Any] = {
        "schema": SCHEMA,
        "generated_at": time.time(),
        "build": {
            "based_on": "buildbot",
            "identity": (identity or {}).get("identity", "custom"),
            "name": (identity or {}).get("name", ""),
        },
        "hardware": census,
        "brain": brain or {"model": None, "recommended": None, "chosen_by": None},
        "crew": crew or {"master_core": None, "max_specialists": 0, "selected_specialists": []},
        "storage": storage or {"media_dir": None, "knowledge_dir": None, "vault_dir": None},
        "knowledge": knowledge or {"zim_dir": None, "auto_index": True, "packs": []},
        "egress": egress or {"posture": "all-offline", "enabled": []},
        "integrations": integrations or {"home_assistant": False, "media_server": False, "devices": []},
        "posture": posture or _default_posture_block(),
        "surfaces": surfaces or {
            "allowlist": [],
            "reasons": {},
            "sensitive": [],
            "derived_from_goal": False,
            "owner_edited": False,
            "note": "Surfaces outside the allowlist are not loaded.",
        },
        "provenance": provenance or {"source": "local", "bundle": None},
        "contracts": dict(_CONTRACTS),
    }
    health = config_health.manifest_block()
    if health is not None:
        manifest["config_health"] = health
    return manifest


def _default_posture_block() -> dict[str, Any]:
    """The skip-this-step posture, marked as defaulted rather than chosen."""
    from puffpai import build_posture
    return build_posture.posture_manifest_block(build_posture.assess_posture())


def bundle_provenance_block(
    bundle_record: dict[str, Any],
    verified: bool = False,
) -> dict[str, Any]:
    """Provenance for a build that arrived on removable media.

    Records which bundle this build came out of and that every checksum was
    verified before anything was applied. Only fields we control are copied
    across - a bundle does not get to write arbitrary keys into the manifest.
    """
    record = bundle_record or {}
    return {
        "source": "bundle",
        "bundle": {
            "bundle_id": str(record.get("bundle_id") or ""),
            "label": str(record.get("label") or ""),
            "created_at": record.get("created_at"),
            "algorithm": str(record.get("algorithm") or "sha256"),
            "file_count": len(record.get("payload_files") or []),
            "checksums_verified": bool(verified),
            "network_used": False,
        },
    }


# ---------------------------------------------------------------------------
# validation (the contract guard)
# ---------------------------------------------------------------------------

def _scan_forbidden(obj: Any, path: str, hits: list[str], secrets: list[str]) -> None:
    """Recursively collect forbidden contract keys and stray secrets.

    Recurses through lists as well as dicts, because the new v2 blocks hold
    lists of dicts (a surface's reasons, a bundle's files) and a key smuggled
    three levels down inside a list is exactly as dangerous as one at the top.
    """
    if isinstance(obj, dict):
        for key, value in obj.items():
            here = f"{path}.{key}" if path else str(key)
            if here == "contracts":
                # The contracts block legitimately contains the word
                # "telemetry" as the name of a contract, and sanitize_manifest
                # overwrites the whole block from _CONTRACTS regardless of what
                # a file says. Scanning it would warn about every valid
                # manifest while changing nothing. Skip it entirely — including
                # recursion, so a key smuggled inside cannot be reported as if
                # the manifest configured something.
                continue
            if key in _FORBIDDEN_KEYS:
                hits.append(here)
            elif key in _SECRET_KEYS:
                secrets.append(here)
            _scan_forbidden(value, here, hits, secrets)
    elif isinstance(obj, list):
        for i, value in enumerate(obj):
            _scan_forbidden(value, f"{path}[{i}]", hits, secrets)


def validate_manifest(manifest: dict[str, Any]) -> list[str]:
    """Return a list of warnings; empty means clean.

    Flags any smuggled contract-disabling key. Callers strip what this finds
    (see :func:`sanitize_manifest`) — a manifest can never turn a contract off.
    """
    warnings: list[str] = []
    hits: list[str] = []
    secrets: list[str] = []
    _scan_forbidden(manifest, "", hits, secrets)
    for hit in hits:
        warnings.append(
            f"Ignoring '{hit}': the contracts (vault, consent, gates, no telemetry) "
            f"are not manifest-configurable."
        )
    for hit in secrets:
        warnings.append(
            f"Dropping '{hit}': a manifest records that the vault exists, never "
            f"a PIN, a recovery code, or a token."
        )
    warnings.extend(_posture_warnings(manifest))

    schema = manifest.get("schema")
    if schema == SCHEMA:
        pass
    elif schema in SUPPORTED_SCHEMAS:
        warnings.append(
            f"Manifest schema is '{schema}'; this build writes '{SCHEMA}'. It "
            f"reads fine, and a re-run fills in the newer blocks."
        )
    else:
        warnings.append(
            f"Manifest schema is '{schema}', expected one of "
            f"{', '.join(SUPPORTED_SCHEMAS)}."
        )
    return warnings


def _posture_warnings(manifest: dict[str, Any]) -> list[str]:
    """Flag a posture block whose values are not ones we recognise.

    A hand-edited ``run_egress: "unrestricted"`` must not read as permission for
    anything. :func:`sanitize_manifest` coerces it back to the safe default; this
    is where the owner gets told that happened.
    """
    from puffpai import build_posture

    block = manifest.get("posture")
    if not isinstance(block, dict):
        return []
    out: list[str] = []
    net = block.get("build_network")
    if net is not None and net not in ("online", "offline"):
        out.append(
            f"Posture build_network '{net}' is not one this build knows; "
            f"treating it as 'offline'."
        )
    egress = block.get("run_egress")
    if egress is not None and egress not in ("sealed", "island", "enumerated"):
        out.append(
            f"Posture run_egress '{egress}' is not one this build knows; "
            f"treating it as '{build_posture.DEFAULT_RUN_EGRESS}'."
        )
    switches = block.get("egress_switches")
    if isinstance(switches, dict):
        known = build_posture.switch_ids()
        for key in sorted(str(k) for k in switches):
            if key not in known:
                out.append(f"Posture door '{key}' is not a door this build has; ignoring it.")
    return out


def sanitize_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    """Return a copy with any forbidden contract-disabling keys removed.

    Deep copy via JSON round-trip (manifests are plain JSON), then drop every
    forbidden key and every stray secret wherever it appears, at any depth,
    inside dicts and inside lists of dicts alike. The contracts block is always
    reset to the canonical text so a manifest cannot misrepresent its own
    guarantees.

    Two v2 additions harden the same rule against the new blocks. The posture is
    normalised to values this build actually implements - an unrecognised
    ``run_egress`` becomes the safe default rather than being honoured, and a
    door the build does not have is dropped rather than believed. The surface
    allowlist is coerced to a list of plain strings, because an allowlist is a
    set of names, and anything richer is somebody trying something.
    """
    try:
        clone = json.loads(json.dumps(manifest))
    except (TypeError, ValueError):
        clone = dict(manifest)

    def _strip(obj: Any) -> Any:
        if isinstance(obj, dict):
            return {k: _strip(v) for k, v in obj.items() if k not in _STRIPPED_KEYS}
        if isinstance(obj, list):
            return [_strip(v) for v in obj]
        return obj

    cleaned = _strip(clone)
    if isinstance(cleaned, dict):
        cleaned["contracts"] = dict(_CONTRACTS)
        # config_health is regenerated, never trusted. An imported bundle must
        # not be able to claim health it does not have (dropping a degradation
        # the importing box is actually running under) nor to invent one. Like
        # contracts, it is a statement this build makes about itself, so it is
        # always overwritten by the live record - and removed outright when
        # every first-party config reads clean.
        health = config_health.manifest_block()
        if health is None:
            cleaned.pop("config_health", None)
        else:
            cleaned["config_health"] = health
        if isinstance(cleaned.get("posture"), dict):
            cleaned["posture"] = _normalize_posture(cleaned["posture"])
        if isinstance(cleaned.get("surfaces"), dict):
            cleaned["surfaces"] = _normalize_surfaces(cleaned["surfaces"])
    return cleaned  # type: ignore[no-any-return]


def _normalize_posture(block: dict[str, Any]) -> dict[str, Any]:
    """Coerce a posture block to values this build implements. Fails safe."""
    from puffpai import build_posture

    net = block.get("build_network")
    # An unrecognised build_network fails to the *restrictive* side: if we cannot
    # tell whether the wizard may reach the network, it may not.
    if net not in ("online", "offline"):
        net = "offline"
    egress = block.get("run_egress")
    if egress not in ("sealed", "island", "enumerated"):
        egress = build_posture.DEFAULT_RUN_EGRESS

    known = build_posture.switch_ids()
    raw = block.get("egress_switches")
    table: dict[str, bool] = {sid: False for sid in sorted(known)}
    if egress == "enumerated" and isinstance(raw, dict):
        for key, value in raw.items():
            sid = str(key)
            if sid in known:
                table[sid] = value is True

    out = dict(block)
    out["build_network"] = net
    out["run_egress"] = egress
    out["egress_switches"] = table
    out["enabled_switches"] = sorted(sid for sid, on in table.items() if on)
    out["combination"] = build_posture.describe_combination(net, egress)
    return out


def _normalize_surfaces(block: dict[str, Any]) -> dict[str, Any]:
    """Coerce the surface allowlist to a plain, deduplicated list of names."""
    raw = block.get("allowlist")
    names: list[str] = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, (str, int, float)):
                name = str(item).strip()
                if name and name not in names:
                    names.append(name)
    out = dict(block)
    out["allowlist"] = sorted(names)
    reasons = block.get("reasons")
    out["reasons"] = (
        {str(k): str(v) for k, v in reasons.items() if str(k) in names}
        if isinstance(reasons, dict) else {}
    )
    sensitive = block.get("sensitive")
    out["sensitive"] = (
        sorted({str(s) for s in sensitive if str(s) in names})
        if isinstance(sensitive, list) else []
    )
    return out


# ---------------------------------------------------------------------------
# read / write
# ---------------------------------------------------------------------------

def read_manifest(path: Optional[str] = None) -> Optional[dict[str, Any]]:
    """Load the existing manifest, or None if there is not one yet."""
    target = path or manifest_path()
    try:
        with open(target, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def write_manifest(manifest: dict[str, Any], path: Optional[str] = None) -> str:
    """Sanitize then write the manifest. Returns the path.

    A write is a primitive: the wizard is responsible for reading any existing
    manifest and showing :func:`diff_manifest` before calling this. Sanitizing
    here is the backstop that makes the contract rule true on disk, not just in
    the flow.
    """
    target = path or manifest_path()
    clean = sanitize_manifest(manifest)
    if path is None:
        # This bot's first apps on-set is settled from the build as it was,
        # before the new one is written: a new bot's own page list is never
        # read as an older bot's use (puffpai.first_apps).
        from puffpai import first_apps
        first_apps.settle()
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "w", encoding="utf-8") as fh:
        json.dump(clean, fh, indent=2)
        fh.write("\n")
    return target


# ---------------------------------------------------------------------------
# diff (never overwrite blindly)
# ---------------------------------------------------------------------------

def _get(obj: dict[str, Any], *keys: str) -> Any:
    """Nested get that tolerates missing intermediates."""
    cur: Any = obj
    for key in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def diff_manifest(old: Optional[dict[str, Any]], new: dict[str, Any]) -> list[str]:
    """Human-readable diff of the fields that matter, old -> new.

    Empty list means nothing meaningful changed. Used by the wizard to show what
    a re-run would change before it writes anything.
    """
    if old is None:
        return ["New build manifest (no previous one to compare)."]

    lines: list[str] = []

    def compare(label: str, old_val: Any, new_val: Any) -> None:
        if old_val != new_val:
            lines.append(f"{label}: {old_val!r} -> {new_val!r}")

    compare("Name", _get(old, "build", "name"), _get(new, "build", "name"))
    compare("Build type", _get(old, "build", "identity"), _get(new, "build", "identity"))
    compare("Memory (GB)", _get(old, "hardware", "memory", "total_gb"),
            _get(new, "hardware", "memory", "total_gb"))
    compare("Hardware class", _get(old, "hardware", "hardware_class"),
            _get(new, "hardware", "hardware_class"))
    compare("Brain", _get(old, "brain", "model"), _get(new, "brain", "model"))
    compare("Max specialists", _get(old, "crew", "max_specialists"),
            _get(new, "crew", "max_specialists"))
    compare("Egress posture", _get(old, "egress", "posture"), _get(new, "egress", "posture"))
    compare("Egress enabled", _get(old, "egress", "enabled"), _get(new, "egress", "enabled"))
    compare("Knowledge packs", _get(old, "knowledge", "packs"), _get(new, "knowledge", "packs"))
    compare("Build network", _get(old, "posture", "build_network"),
            _get(new, "posture", "build_network"))
    compare("Runtime egress", _get(old, "posture", "run_egress"),
            _get(new, "posture", "run_egress"))
    compare("Outside services on", _get(old, "posture", "enabled_switches"),
            _get(new, "posture", "enabled_switches"))
    compare("Surfaces loaded", _get(old, "surfaces", "allowlist"),
            _get(new, "surfaces", "allowlist"))
    compare("Build source", _get(old, "provenance", "source"),
            _get(new, "provenance", "source"))

    return lines


# ---------------------------------------------------------------------------
# display
# ---------------------------------------------------------------------------

def _page_names(allowlist: Any) -> list[str]:
    """Pages in the owner's words (plain-words rule), not ids."""
    try:
        from puffpai.build_interview import surface_taxonomy
        taxonomy = surface_taxonomy() or {}
    except Exception:
        taxonomy = {}
    return [str((taxonomy.get(str(s)) or {}).get("label")
                or "your bot's own page") for s in allowlist]


def _running_words(run_egress: str, switched: int) -> str:
    """What the finished bot may reach, in words (never the setting's name)."""
    if run_egress == "sealed":
        return "While it runs, it talks to nothing outside this computer."
    if run_egress == "enumerated":
        return ("While it runs, it may use only the outside services you said "
                "yes to" + (f" ({switched} of the built-in ones switched on)."
                            if switched else "."))
    return "While it runs, it stays on your home network."


def manifest_summary_lines(manifest: dict[str, Any]) -> list[str]:
    """Plain-language lines summarising a manifest, for the CLI/API."""
    lines: list[str] = []
    name = _get(manifest, "build", "name")
    identity = _get(manifest, "build", "identity")
    lines.append(f"Your bot: {name or 'not named yet'}, made with BuildBot")

    ram = _get(manifest, "hardware", "memory", "total_gb")
    if ram is not None:
        lines.append(f"This computer: {ram:g} GB of memory" if isinstance(
            ram, (int, float)) else f"This computer: {ram} GB of memory")

    brain = _get(manifest, "brain", "model")
    if brain:
        lines.append(f"Its brain: {brain}")

    # Only speak to the helpers once sizing has actually run. Before it does,
    # the block defaults to zero, and "up to 0" reports a question nobody
    # answered as a limit nobody has.
    specialists = _get(manifest, "crew", "max_specialists")
    if specialists is not None and _get(manifest, "crew", "master_core") is not None:
        lines.append(f"Helpers: the main team, and up to {specialists} more "
                     f"for bigger jobs")

    # The v2 posture block supersedes the v1 egress block. Printing both makes
    # the summary contradict itself - a build could read "Egress: all-offline"
    # and "runs island" two lines apart, which is the one place a manifest
    # summary must not be ambiguous. Newer wins; the legacy line is the
    # fallback for a v1 manifest that has no posture.
    stance = _get(manifest, "posture", "run_egress")
    if stance:
        network = _get(manifest, "posture", "build_network")
        switched = _get(manifest, "posture", "enabled_switches") or []
        lines.append(("Built while online. " if network == "online"
                      else "Built without the internet. ")
                     + _running_words(str(stance), len(switched)))
    else:
        enabled = _get(manifest, "egress", "enabled") or []
        lines.append("Internet while it runs: " + (
            f"{len(enabled)} outside service(s) switched on" if enabled
            else "no outside service is switched on"))

    allowlist = _get(manifest, "surfaces", "allowlist")
    if allowlist is not None:
        lines.append(
            f"Pages it will have: {', '.join(_page_names(allowlist))}"
            if allowlist else
            "Pages it will have: none of the optional ones."
        )

    source = _get(manifest, "provenance", "source")
    if source == "bundle":
        bundle_id = _get(manifest, "provenance", "bundle", "bundle_id") or "unknown"
        lines.append(f"Came from a provisioning bundle ({bundle_id}), checksums verified.")

    health = manifest.get("config_health")
    if isinstance(health, dict) and health.get("degraded"):
        configs = health.get("configs") or []
        names = ", ".join(str(c).replace("_", " ") for c in configs) or "a config file"
        lines.append(
            f"Heads up: this build was assembled while {names} could not be read, "
            f"so it may be missing options. Fix the config and re-run setup."
        )

    return lines


__all__ = [
    "SCHEMA",
    "SUPPORTED_SCHEMAS",
    "manifest_path",
    "assemble_manifest",
    "bundle_provenance_block",
    "validate_manifest",
    "sanitize_manifest",
    "read_manifest",
    "write_manifest",
    "diff_manifest",
    "manifest_summary_lines",
]
